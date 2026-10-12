"""来源接口真实 MySQL/TCP 验收；RR 单项显式启用，无模型或对象 I/O。"""

from __future__ import annotations

import base64
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event, Thread
from uuid import uuid4

import httpx
import pytest
import uvicorn
from sqlalchemy import event, select, text

from short_drama.core.config import Settings
from short_drama.db.readiness import assert_identity_ready
from short_drama.db.session import AppSession, session_factory
from short_drama.domain import Episode, EpisodeNovel, Project, ProjectCanvas
from short_drama.main import create_app
from tests.integration.test_canvas_character_rr import WAIT_SQL, RRCase, wait_for_lock
from tests.integration.test_identity_collaboration import PASSWORD, code_for, join

pytestmark = pytest.mark.integration
REQUEST_KIND = ContextVar("source_project_read_http_kind", default="other")
ORIGINAL = "  第一行\r\n<script>正文原文，不解释为 HTML</script>\n尾行  "


class ObservedHTTP:
    """仅观测测试自有请求头与响应时序，完整透传正式应用。"""

    def __init__(self, app):
        self.app = app
        self.audit = None

    async def __call__(self, scope, receive, send):
        kind = dict(scope.get("headers", [])).get(b"x-source-read-observation", b"other")
        token = REQUEST_KIND.set(kind.decode("ascii"))

        async def observed_send(message):
            if self.audit is not None and REQUEST_KIND.get() == "reader":
                if message["type"] == "http.response.start":
                    self.audit["response_start_ns"] = time.monotonic_ns()
                    self.audit["response_status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            REQUEST_KIND.reset(token)


@dataclass
class SourceHTTP:
    factory: object
    settings: Settings
    observed: ObservedHTTP
    url: str
    clients: list = field(default_factory=list)

    def account(self):
        username = "source_http_" + uuid4().hex[:16]
        client = httpx.Client(
            base_url=self.url,
            timeout=30,
            trust_env=False,
            headers={"Origin": self.settings.public_origin},
        )
        self.clients.append(client)
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "display_name": "来源接口验收",
                "email": username + "@example.test",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 201, response.text
        challenge = response.json()["challenge_id"]
        response = client.post(
            "/api/v1/auth/verification/confirm",
            json={
                "challenge_id": challenge,
                "code": code_for(self.factory, self.settings, challenge),
            },
        )
        assert response.status_code == 200, response.text
        response = client.post(
            "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = client.cookies["sd_csrf"]
        return client, response.json()["user"]


@pytest.fixture
def source_http(db_session, mysql_engine, request):
    assert db_session.get_bind() is mysql_engine
    assert mysql_engine.dialect.name == "mysql" and mysql_engine.url.database.endswith("_test")
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        agent_enabled=False,
        auth_cookie_secure=False,
        public_origin=getattr(request, "param", {}).get(
            "public_origin", "http://source-read.example.test"
        ),
        encryption_key=base64.b64encode(b"s" * 32).decode(),
        smtp_host="",
        smtp_from="",
    )
    factory = session_factory(mysql_engine)
    assert_identity_ready(mysql_engine, settings)
    app = create_app(settings)
    app.state.session_factory, app.state.storage = factory, None
    observed = ObservedHTTP(app)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                observed,
                host="127.0.0.1",
                port=port,
                lifespan="off",
                access_log=False,
                log_config=None,
                log_level="error",
            )
        )
        errors = []

        def serve():
            try:
                server.run(sockets=[listener])
            except BaseException as error:
                errors.append(type(error).__name__)

        thread = Thread(target=serve, name="source-read-test-api", daemon=True)
        runtime = SourceHTTP(factory, settings, observed, f"http://127.0.0.1:{port}")
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                Event().wait(0.05)
            assert server.started, f"隔离来源 API 未启动：{errors}"
            yield runtime
        finally:
            for client in reversed(runtime.clients):
                client.close()
            server.should_exit = True
            thread.join(5)
            if thread.is_alive():
                server.force_exit = True
                thread.join(5)
            assert not thread.is_alive(), "隔离来源 API 线程未退出"
            assert not errors, errors


def created(client, path, payload):
    response = client.post(
        path, json=payload, headers={"Idempotency-Key": "source-read-" + uuid4().hex}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def bound_source(source_http):
    owner, user = source_http.account()
    source = created(
        owner,
        "/api/v1/projects",
        {"name": "可信来源", "aspect": "9:16", "synopsis": "来源梗概", "style": "来源风格"},
    )
    owning = created(
        owner,
        "/api/v1/projects",
        {"name": "画布所属", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    canvas_path = f"/api/v1/projects/{owning['id']}/canvases/{owning['primary_canvas_id']}"
    response = owner.get(canvas_path + "/my-document")
    assert response.status_code == 200, response.text
    document = response.json()["source_document"]
    # 仅在随机测试库中种入可信绑定；不伪造 HTTP source_project_id 或 Actor。
    with source_http.factory.begin() as session:
        session.get(ProjectCanvas, int(owning["primary_canvas_id"])).source_project_id = int(
            source["id"]
        )
    episodes = []
    for title, content in (("原文", ORIGINAL), ("空正文", ""), ("无小说记录", None)):
        episode = created(owner, f"/api/v1/projects/{source['id']}/episodes", {"title": title})
        if content is not None:
            response = owner.put(
                f"/api/v1/projects/{source['id']}/episodes/{episode['id']}/novel",
                json={"content_version": episode["content_version"], "content": content},
            )
            assert response.status_code == 200, response.text
            episode["content_version"] = response.json()["content_version"]
        else:
            response = owner.put(
                f"/api/v1/projects/{source['id']}/episodes/{episode['id']}/script",
                json={
                    "content_version": episode["content_version"],
                    "script_id": None,
                    "content": "已保存剧本不能补作小说正文",
                },
            )
            assert response.status_code == 200, response.text
            episode["content_version"] = response.json()["content_version"]
        episodes.append(episode)
    params = {
        "expected_canvas_row_version": str(document["revision"]),
        "expected_owning_project_id": owning["id"],
        "expected_source_project_id": source["id"],
    }
    return {
        "owner": owner,
        "user": user,
        "source": source,
        "owning": owning,
        "document": document,
        "canvas_path": canvas_path,
        "path": f"/api/v1/canvas-runtime/canvas-projects/{document['id']}/source-project",
        "params": params,
        "episodes": episodes,
    }


def read_body(case, episode, *, client=None, params=None, headers=None):
    return (client or case["owner"]).get(
        case["path"] + f"/episodes/{episode['id']}",
        params={
            **(params or case["params"]),
            "expected_content_version": episode["content_version"],
        },
        headers=headers,
    )


def business_state(factory, case):
    with factory() as session:
        canvas = session.get(ProjectCanvas, int(case["owning"]["primary_canvas_id"]))
        episodes = session.scalars(
            select(Episode)
            .where(Episode.project_id == int(case["source"]["id"]))
            .order_by(Episode.position, Episode.id)
        ).all()
        return {
            "canvas": {
                "id": str(canvas.id),
                "project_id": str(canvas.project_id),
                "source_project_id": str(canvas.source_project_id),
                "row_version": canvas.row_version,
            },
            "episodes": [
                {
                    "id": str(episode.id),
                    "position": episode.position,
                    "content_version": episode.content_version,
                    "content": session.scalar(
                        select(EpisodeNovel.content).where(EpisodeNovel.episode_id == episode.id)
                    ),
                }
                for episode in episodes
            ],
        }


def test_source_overview_and_original_empty_missing_novel_are_read_only(source_http, bound_source):
    case = bound_source
    # 制造真实排序空洞及与ID顺序不同的position，读取须保留数据库正数位置。
    with source_http.factory.begin() as session:
        session.get(Episode, int(case["episodes"][0]["id"])).position = 4
    case["episodes"][0]["position"] = 4
    before = business_state(source_http.factory, case)
    response = case["owner"].get(case["path"], params=case["params"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["canvas_key"] == case["document"]["id"]
    assert result["canvas_row_version"] == case["params"]["expected_canvas_row_version"]
    assert result["owning_project_id"] == case["owning"]["id"]
    assert result["source_project_id"] == case["source"]["id"]
    assert result["project"] == {
        "id": case["source"]["id"],
        "row_version": case["source"]["row_version"],
        "name": "可信来源",
        "synopsis": "来源梗概",
        "aspect": "9:16",
        "style_text": "来源风格",
    }
    assert result["capabilities"] == {
        "source_style_snapshot": "unavailable",
        "source_asset_folders": "unavailable",
        "source_assets": "not_included",
    }
    assert set(result) == {
        "canvas_key",
        "canvas_row_version",
        "owning_project_id",
        "source_project_id",
        "project",
        "episodes",
        "capabilities",
    }
    assert [row["position"] for row in result["episodes"]] == [2, 3, 4]
    assert [row["id"] for row in result["episodes"]] == [
        row["id"] for row in sorted(case["episodes"], key=lambda row: row["position"])
    ]
    for episode, expected in zip(case["episodes"], (ORIGINAL, "", None), strict=True):
        body = read_body(case, episode)
        assert body.status_code == 200, body.text
        assert body.json()["novel"] == {
            "state": "missing" if expected is None else "present",
            "content_format": "plain_text",
            "plain_text": expected,
        }
        assert body.json()["episode"] == {
            "id": episode["id"],
            "title": episode["title"],
            "position": episode["position"],
            "content_version": episode["content_version"],
            "kind": "episode",
        }
    assert business_state(source_http.factory, case) == before


def test_source_expected_identity_revision_body_version_and_episode_scope(bound_source):
    case = bound_source
    for expected_field, code in (
        ("expected_canvas_row_version", "canvas_revision_conflict"),
        ("expected_owning_project_id", "canvas_source_context_conflict"),
        ("expected_source_project_id", "canvas_source_context_conflict"),
    ):
        params = {**case["params"], expected_field: "18446744073709551615"}
        response = case["owner"].get(case["path"], params=params)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == code
        body = read_body(case, case["episodes"][0], params=params)
        assert body.status_code == 409 and body.json()["error"]["code"] == code
    response = case["owner"].get(case["path"])
    assert response.status_code == 422, response.text
    response = case["owner"].get(
        case["path"] + f"/episodes/{case['episodes'][0]['id']}", params=case["params"]
    )
    assert response.status_code == 422, response.text
    stale = {**case["episodes"][0], "content_version": "18446744073709551615"}
    response = read_body(case, stale)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "canvas_source_content_conflict"
    other = created(case["owner"], "/api/v1/projects", {"name": "另一项目", "aspect": "16:9"})
    foreign = created(
        case["owner"], f"/api/v1/projects/{other['id']}/episodes", {"title": "外部同名分集"}
    )
    assert read_body(case, foreign).status_code == 404


def test_source_missing_or_self_binding_does_not_grant_source_access(source_http, bound_source):
    case = bound_source
    for binding, code in (
        (None, "canvas_source_project_required"),
        (int(case["owning"]["id"]), "canvas_binding_invalid"),
    ):
        with source_http.factory.begin() as session:
            session.get(
                ProjectCanvas, int(case["owning"]["primary_canvas_id"])
            ).source_project_id = binding
        for response in (
            case["owner"].get(case["path"], params=case["params"]),
            read_body(case, case["episodes"][0]),
        ):
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("target", ["owning", "source"])
def test_source_requires_permission_on_both_projects_even_with_expected_ids(
    source_http, bound_source, target
):
    case = bound_source
    member, user = source_http.account()
    for project in (case["owning"], case["source"]):
        join(
            (None, source_http.factory, source_http.settings),
            case["owner"],
            member,
            project["id"],
            user["id"],
        )
    assert member.get(case["path"], params=case["params"]).status_code == 200
    assert read_body(case, case["episodes"][0], client=member).status_code == 200
    response = case["owner"].delete(f"/api/v1/projects/{case[target]['id']}/members/{user['id']}")
    assert response.status_code == 200, response.text
    params = {**case["params"], "system": "true", "source_project_id": case["source"]["id"]}
    assert member.get(case["path"], params=params).status_code == 404
    assert read_body(case, case["episodes"][0], client=member, params=params).status_code == 404
    with httpx.Client(base_url=source_http.url, trust_env=False) as anonymous:
        assert anonymous.get(case["path"], params=params).status_code == 401


@pytest.mark.parametrize("target", ["owning", "source", "canvas"])
def test_source_rejects_archived_context(source_http, bound_source, target):
    case = bound_source
    if target == "canvas":
        response = case["owner"].request(
            "DELETE",
            case["canvas_path"],
            headers={"Idempotency-Key": "source-archive-" + uuid4().hex},
            json={"expected_row_version": case["params"]["expected_canvas_row_version"]},
        )
        assert response.status_code == 200, response.text
    else:
        response = case["owner"].delete(f"/api/v1/projects/{case[target]['id']}")
        assert response.status_code == 204, response.text
    assert case["owner"].get(case["path"], params=case["params"]).status_code == 404
    assert read_body(case, case["episodes"][0]).status_code == 404


@pytest.mark.parametrize("target", ["owning", "source"])
def test_source_rejects_invalid_project_modes(source_http, bound_source, target):
    case = bound_source
    # 非法模式关系不能经公共API建立，仅在随机测试库种入旧库异常关系。
    with source_http.factory.begin() as session:
        row = session.get(Project, int(case[target]["id"]))
        row.workspace_mode = "standard" if target == "owning" else "infinite_canvas"
    for response in (
        case["owner"].get(case["path"], params=case["params"]),
        read_body(case, case["episodes"][0]),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "canvas_binding_invalid"


@pytest.mark.parametrize("kind", ["revision", "content"])
@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_SOURCE_READ_RR") != "1",
    reason="RUN_CANVAS_SOURCE_READ_RR=1 显式启用实际 RR 锁等待与回滚观测",
)
def test_source_real_rr_version_race_rolls_back_before_exact_http_conflict(
    source_http, bound_source, mysql_engine, kind, tmp_path, request
):
    case = bound_source
    with mysql_engine.connect() as observer:
        assert observer.scalar(text("SELECT @@performance_schema")) == 1
        observer.execute(WAIT_SQL, {"database": mysql_engine.url.database, "connection": -1}).all()
    before = business_state(source_http.factory, case)
    source_id, owning_id = int(case["source"]["id"]), int(case["owning"]["id"])
    canvas_id, episode_id = int(case["owning"]["primary_canvas_id"]), int(case["episodes"][0]["id"])
    ready, release = Event(), Event()
    audit = {"kind": kind, "transactions": [], "reader_dml": [], "before": before}
    pending, transactions, connection_rows, connections, listeners = {}, {}, {}, [], []
    source_http.observed.audit = audit

    def begun(actual, transaction, connection):
        role = REQUEST_KIND.get()
        if role not in {"writer", "reader"} or not actual.info.get("actor"):
            return
        row = {
            "kind": role,
            "session": id(actual),
            "request_project": (
                str(actual.info["request_project"])
                if actual.info.get("request_project") is not None
                else None
            ),
            "connection": connection.scalar(text("SELECT CONNECTION_ID()")),
            "isolation": connection.scalar(text("SELECT @@session.transaction_isolation")),
            "outcome": "active",
            "ended": False,
        }
        assert row["isolation"] == "REPEATABLE-READ"
        if role == "reader":
            # 被动 SQL 观测真实旧快照；不替换 Service/DAO、不注入权限或响应。
            row["old_version"] = connection.scalar(
                select(ProjectCanvas.row_version).where(ProjectCanvas.id == canvas_id)
                if kind == "revision"
                else select(Episode.content_version).where(Episode.id == episode_id)
            )
            connections[:] = [row["connection"]]
        transactions[id(actual)] = row
        connection_rows[id(connection)] = row
        audit["transactions"].append(row)

    def completed(actual, outcome):
        if id(actual) in transactions:
            row = transactions[id(actual)]
            row.update(outcome=outcome, outcome_ns=time.monotonic_ns())

    def ended(actual, transaction):
        if transaction.parent is None and id(actual) in transactions:
            row = transactions[id(actual)]
            assert not actual.in_transaction(), "完整外层事务结束后仍残留事务"
            row.update(ended=True, end_ns=time.monotonic_ns())
            row["pending_objects"] = len(actual.new) + len(actual.dirty) + len(actual.deleted)

    def before_commit(actual):
        if REQUEST_KIND.get() != "writer" or ready.is_set() or id(actual) not in transactions:
            return
        target_type, target_id = (
            (ProjectCanvas, canvas_id) if kind == "revision" else (Episode, episode_id)
        )
        target = next(
            (
                row
                for row in actual.identity_map.values()
                if isinstance(row, target_type) and row.id == target_id
            ),
            None,
        )
        if target is None:
            return
        actual.flush()
        pending.update(session=id(actual), connection=transactions[id(actual)]["connection"])
        audit["writer_commit_gate"] = dict(pending)
        ready.set()
        assert release.wait(20), "合法 writer 最后 commit 事件门超时"

    def statement(connection, _cursor, sql, _parameters, _context, _many):
        row = connection_rows.get(id(connection))
        if (
            REQUEST_KIND.get() == "reader"
            and row
            and row["kind"] == "reader"
            and sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ):
            audit["reader_dml"].append(sql.split()[0].upper())

    listeners = [
        (AppSession, "after_begin", begun),
        (AppSession, "after_commit", lambda actual: completed(actual, "committed")),
        (AppSession, "after_rollback", lambda actual: completed(actual, "rolled_back")),
        (AppSession, "after_transaction_end", ended),
        (AppSession, "before_commit", before_commit),
        (mysql_engine, "before_cursor_execute", statement),
    ]
    reader = httpx.Client(base_url=source_http.url, timeout=30, trust_env=False)
    reader.cookies.update(case["owner"].cookies)
    reader.headers.update(case["owner"].headers)
    source_http.clients.append(reader)
    try:
        for target, name, callback in listeners:
            event.listen(target, name, callback)
        with ThreadPoolExecutor(max_workers=2) as pool:
            if kind == "revision":
                changed = deepcopy(case["document"])
                changed["title"] = "并发合法 autosave 后标题"
                writer = pool.submit(
                    case["owner"].post,
                    case["canvas_path"] + "/commits",
                    headers={
                        "Idempotency-Key": "source-read-race-" + uuid4().hex,
                        "X-Source-Read-Observation": "writer",
                    },
                    json={
                        "expected_row_version": before["canvas"]["row_version"],
                        "source_document": changed,
                    },
                )
            else:
                writer = pool.submit(
                    case["owner"].put,
                    f"/api/v1/projects/{source_id}/episodes/{episode_id}/novel",
                    headers={"X-Source-Read-Observation": "writer"},
                    json={
                        "content_version": case["episodes"][0]["content_version"],
                        "content": "合法并发新正文",
                    },
                )
            try:
                if not ready.wait(10):
                    if writer.done():
                        failed = writer.result()
                        audit["writer_before_wait"] = {
                            "status": failed.status_code,
                            "body": failed.json(),
                        }
                    raise AssertionError("合法 writer 未到最后 commit，未形成真实竞争")
                raced = pool.submit(
                    read_body,
                    case,
                    case["episodes"][0],
                    client=reader,
                    headers={"X-Source-Read-Observation": "reader"},
                )
                rr = RRCase(
                    mysql_engine, source_id, owning_id, canvas_id, 0, 0, case["document"]["id"]
                )
                wait_for_lock(
                    rr,
                    connections,
                    {("projects", owning_id if kind == "revision" else source_id)},
                    pending["connection"],
                )
                audit["waits"] = rr.observations
                release.set()
                written, response = writer.result(timeout=15), raced.result(timeout=15)
            finally:
                release.set()
        assert written.status_code == 200, written.text
        audit["writer_status"], audit["raced_response"] = written.status_code, response.json()
        assert response.status_code == 409, response.text
        code = (
            "canvas_revision_conflict" if kind == "revision" else "canvas_source_content_conflict"
        )
        assert response.json()["error"]["code"] == code, response.text
        readers = [row for row in audit["transactions"] if row["kind"] == "reader"]
        writers = [
            row
            for row in audit["transactions"]
            if row["kind"] == "writer"
            and row["session"] == pending["session"]
            and row["connection"] == pending["connection"]
        ]
        scope_lookups = [
            row for row in audit["transactions"] if row["kind"] == "writer" and row not in writers
        ]
        audit["writer_resource_scope_lookups"] = scope_lookups
        assert len(readers) == len(writers) == 1, audit["transactions"]
        assert writers[0]["request_project"] == str(owning_id if kind == "revision" else source_id)
        assert len(scope_lookups) == 1, audit["transactions"]
        lookup = scope_lookups[0]
        assert lookup["request_project"] is None and lookup["ended"]
        assert lookup["pending_objects"] == 0
        assert lookup["end_ns"] < writers[0]["outcome_ns"]
        old = (
            before["canvas"]["row_version"]
            if kind == "revision"
            else before["episodes"][0]["content_version"]
        )
        assert readers[0]["old_version"] == old
        assert readers[0]["outcome"] == "rolled_back" and readers[0]["ended"]
        assert readers[0]["pending_objects"] == 0 and not audit["reader_dml"]
        assert readers[0]["outcome_ns"] <= readers[0]["end_ns"] <= audit["response_start_ns"]
        assert writers[0]["outcome"] == "committed" and writers[0]["ended"]
        assert writers[0]["outcome_ns"] < readers[0]["outcome_ns"]
        after = business_state(source_http.factory, case)
        expected = deepcopy(before)
        if kind == "revision":
            expected["canvas"]["row_version"] += 1
            assert response.json()["error"]["details"]["current_version"] == str(old + 1)
        else:
            expected["episodes"][0]["content_version"] += 1
            expected["episodes"][0]["content"] = "合法并发新正文"
        assert after == expected, "只有合法 writer 的业务变动能提交"
        audit["after"] = after
        fresh_params = {
            **case["params"],
            "expected_canvas_row_version": str(after["canvas"]["row_version"]),
        }
        fresh_episode = {
            **case["episodes"][0],
            "content_version": str(after["episodes"][0]["content_version"]),
        }
        fresh = read_body(case, fresh_episode, client=reader, params=fresh_params)
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["novel"]["plain_text"] == after["episodes"][0]["content"]
    finally:
        release.set()
        source_http.observed.audit = None
        for target, name, callback in reversed(listeners):
            if event.contains(target, name, callback):
                event.remove(target, name, callback)
        request.node.user_properties.append(
            ("source_rr_http", json.dumps(audit, ensure_ascii=False))
        )
        (tmp_path / "source-rr-http.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
