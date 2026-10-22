"""Canvas HTTP contracts use real accounts, CSRF middleware and MySQL transactions."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from sqlalchemy import select

from short_drama.dao.canvas_dao import CanvasDAO
from short_drama.db import access
from short_drama.domain.canvas import CanvasNode, CanvasRevision, CanvasUserState
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = pytest.mark.integration


def test_skill_body_roundtrips_privately_without_advancing_work(identity_app):
    owner, _ = account(identity_app, "canvas_skill_owner")
    outsider, _ = account(identity_app, "canvas_skill_outsider")
    created = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "skill-body-project"},
        json={"name": "技能正文隔离", "workspace_mode": "infinite_canvas"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document").json()
    document = initial["source_document"]
    private_body = {
        "content": "本人技能模板，不公开给成员",
        "previewContent": "本人技能预览",
        "richText": {"type": "doc", "content": [{"type": "text", "text": "本人技能正文"}]},
        "skillSnapshot": {"name": "镜头模板", "template": "仅本人安装的技能模板"},
    }
    document["nodes"] = [
        {
            "id": "skill-body",
            "type": "skill",
            "title": "技能引用",
            "position": {"x": 20, "y": 20},
            "width": 360,
            "height": 220,
            "metadata": private_body,
        },
        {
            "id": "work",
            "type": "text",
            "title": "作品",
            "position": {"x": 440, "y": 20},
            "width": 320,
            "height": 220,
            "metadata": {"content": "明确保存的共享作品"},
        },
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "skill-body-initial"},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    own = owner.get(path + "/my-document").json()["source_document"]
    assert own["nodes"][0]["metadata"] == private_body
    shared_before = owner.get(path).json()
    assert shared_before["source_document"]["nodes"][0]["metadata"] == {}
    assert shared_before["source_document"]["nodes"][1]["metadata"]["content"] == (
        "明确保存的共享作品"
    )
    own["nodes"][0]["metadata"]["content"] = "本人更新后的模板"
    changed = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "skill-body-private-change"},
        json={"expected_row_version": own["revision"], "source_document": own},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["row_version"] == saved.json()["row_version"]
    assert owner.get(path).json() == shared_before
    assert (
        owner.get(path + "/my-document").json()["source_document"]["nodes"][0]["metadata"][
            "content"
        ]
        == "本人更新后的模板"
    )
    assert outsider.get(path).status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404


def test_old_skill_body_is_hidden_without_guessing_author_or_overwriting_raw_data(identity_app):
    owner, _ = account(identity_app, "canvas_old_skill_owner")
    _, legacy_user = account(identity_app, "canvas_old_skill_author")
    outsider, _ = account(identity_app, "canvas_old_skill_outsider")
    created = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "old-skill-project"},
        json={"name": "旧技能正文保护", "workspace_mode": "infinite_canvas"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    document = owner.get(path + "/my-document").json()["source_document"]
    document["nodes"] = [
        {
            "id": "old-skill",
            "type": "skill",
            "title": "技能引用",
            "position": {"x": 20, "y": 20},
            "width": 360,
            "height": 220,
            "metadata": {"content": "本人已确认的模板"},
        }
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "old-skill-initial"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    history = owner.get(path + "/revisions").json()["items"][0]
    raw_body = {
        "content": "旧共享模板，不能根据移动节点者推断作者",
        "previewContent": "旧共享预览",
        "richText": {"type": "doc", "content": []},
    }
    with identity_app[1].begin() as session:
        node = session.scalar(
            select(CanvasNode).where(CanvasNode.canvas_id == int(project["primary_canvas_id"]))
        )
        raw_node = {**deepcopy(node.content_json), "metadata": raw_body}
        node.content_json = raw_node
        node.updated_by = int(legacy_user["id"])
        revision = session.get(CanvasRevision, int(history["id"]))
        raw_snapshot = deepcopy(revision.snapshot_json)
        raw_snapshot["nodes"] = deepcopy(document["nodes"])
        raw_snapshot["nodes"][0]["metadata"] = deepcopy(raw_body)
        revision.snapshot_json = raw_snapshot
    for client, expected in ((owner, {"content": "本人已确认的模板"}),):
        shared = client.get(path)
        assert shared.status_code == 200, shared.text
        assert shared.json()["source_document"]["nodes"][0]["metadata"] == {}
        own = client.get(path + "/my-document")
        assert own.status_code == 200, own.text
        assert own.json()["source_document"]["nodes"][0]["metadata"] == expected
        for include_documents in (False, True):
            workspace = client.get(
                "/api/v1/canvas-workspace",
                params={"project_id": project["id"], "include_documents": include_documents},
            )
            assert workspace.status_code == 200, workspace.text
            item = workspace.json()["items"][0]
            nodes = item["source_document"]["nodes"] if include_documents else item["preview_nodes"]
            assert nodes[0]["metadata"] == expected
        historic = client.get(path + f"/revisions/{history['id']}")
        assert historic.status_code == 200, historic.text
        assert historic.json()["source_document"]["nodes"][0]["metadata"] == {}
        for operation, suffix, payload in (
            ("commit", "/commits", {"source_document": own.json()["source_document"]}),
            ("restore", f"/revisions/{history['id']}/restore", {}),
        ):
            blocked = client.post(
                path + suffix,
                headers={"Idempotency-Key": "old-skill-protected-" + operation},
                json={"expected_row_version": saved.json()["row_version"], **payload},
            )
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["error"]["code"] == "canvas_legacy_skill_body_private"
    assert outsider.get(path).status_code == 404
    assert outsider.get(path + f"/revisions/{history['id']}").status_code == 404
    with identity_app[1]() as session:
        assert session.get(CanvasNode, node.id).content_json == raw_node
        assert session.get(CanvasRevision, int(history["id"])).snapshot_json == raw_snapshot
    assert owner.get(path).json()["row_version"] == saved.json()["row_version"]
    with identity_app[1].begin() as session:
        current = session.get(CanvasNode, node.id)
        current.content_json = {**deepcopy(raw_node), "metadata": {}}
    historic_restore = owner.post(
        path + f"/revisions/{history['id']}/restore",
        headers={"Idempotency-Key": "old-skill-historic-protected"},
        json={"expected_row_version": saved.json()["row_version"]},
    )
    assert historic_restore.status_code == 409, historic_restore.text
    assert historic_restore.json()["error"]["code"] == "canvas_legacy_skill_body_private"
    with identity_app[1]() as session:
        assert session.get(CanvasRevision, int(history["id"])).snapshot_json == raw_snapshot
    assert owner.get(path).json()["row_version"] == saved.json()["row_version"]


@pytest.mark.parametrize("waiting_for_graph", [False, True])
def test_first_personal_writes_use_current_state_after_waiting_for_project_lock(
    identity_app, database_errors, monkeypatch, waiting_for_graph
):
    client, _ = account(identity_app, "canvas_personal_mvcc")
    project = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "personal-mvcc"},
        json={"name": "个人状态并发", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    document = client.get(path + "/my-document").json()["source_document"]
    document["title"] = "并发时保存作品"
    original_access = access.require_project
    original_project = CanvasDAO.project
    entered, other_committed = Event(), Event()

    def wait_after_snapshot(session):
        if not entered.is_set():
            # Establish an empty REPEATABLE READ snapshot, just as a scoped
            # authorization read can before the project write lock is acquired.
            assert session.scalar(select(CanvasUserState)) is None
            entered.set()
            assert other_committed.wait(10)

    def access_before_lock(session, project_id, **options):
        if options.get("lock"):
            wait_after_snapshot(session)
        return original_access(session, project_id, **options)

    def project_before_lock(self, project_id, *, lock=False):
        if lock:
            wait_after_snapshot(self.session)
        return original_project(self, project_id, lock=lock)

    monkeypatch.setattr(access, "require_project", access_before_lock)
    monkeypatch.setattr(CanvasDAO, "project", project_before_lock)
    viewport = {"x": 12, "y": 24, "k": 1.25}
    expected = {"x": 0, "y": 0, "k": 1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = (
            pool.submit(
                client.post,
                path + "/commits",
                headers={"Idempotency-Key": "graph-after-state"},
                json={"expected_row_version": document["revision"], "source_document": document},
            )
            if waiting_for_graph
            else pool.submit(
                client.put,
                path + "/viewport",
                json={"expected_viewport": expected, "viewport": viewport},
            )
        )
        assert entered.wait(10)
        try:
            saved = client.put(
                path + "/view-preferences",
                json={
                    "expected_preferences": {"backgroundMode": "dots", "showImageInfo": False},
                    "preferences": {"backgroundMode": "lines", "showImageInfo": True},
                },
            )
            assert saved.status_code == 200, saved.text
        finally:
            other_committed.set()
        result = waiting.result()
    assert result.status_code == 200, (result.text, database_errors)
    state = client.get(path + "/my-document").json()["source_document"]
    assert state["viewport"] == (expected if waiting_for_graph else viewport)
    if waiting_for_graph:
        assert state["title"] == document["title"]
    assert state["backgroundMode"] == "lines" and state["showImageInfo"] is True
    assert database_errors == []


def test_authenticated_canvas_save_conflict_history_and_delete_receipt(identity_app):
    client, user = account(identity_app, "canvas_http")
    client.headers["X-Canvas-Actor"] = user["id"]
    created = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "http-project"},
        json={"name": "画布 HTTP 验证", "workspace_mode": "infinite_canvas"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    invalid_episode = client.post(
        f"/api/v1/projects/{project['id']}/episodes", json={"title": "不创建伪分集"}
    )
    assert invalid_episode.status_code == 404
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    read = client.get(path + "/my-document")
    assert read.status_code == 200, read.text
    canvas = read.json()
    document = canvas["source_document"]
    assert isinstance(document["revision"], str)
    document["nodes"] = [
        {
            "id": "note",
            "type": "text",
            "title": "文字",
            "position": {"x": 10.125, "y": -12.375},
            "width": 320,
            "height": 220,
            "metadata": {"content": "真实数据库", "prompt": "私人指令"},
        }
    ]
    payload = {"expected_row_version": canvas["row_version"], "source_document": document}
    headers = {"Idempotency-Key": "http-save"}
    saved = client.post(path + "/commits", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    assert client.post(path + "/commits", json=payload, headers=headers).json() == saved.json()
    conflict = client.post(
        path + "/commits", json=payload, headers={"Idempotency-Key": "http-stale"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "canvas_revision_conflict"
    assert client.get(path).json()["source_document"]["nodes"][0]["metadata"] == {
        "content": "真实数据库"
    }
    revisions = client.get(path + "/revisions").json()["items"]
    assert revisions and revisions[0]["payload_bytes"] > 0
    deleted = client.request(
        "DELETE",
        path,
        headers={"Idempotency-Key": "http-delete"},
        json={"expected_row_version": saved.json()["row_version"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["project_archived"]
    assert client.get(path).status_code == 404
    receipt = client.get("/api/v1/canvas-write-receipts/http-delete")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["result"] == deleted.json()


def test_canvas_actor_switch_stops_request_without_revoking_new_session(identity_app):
    client, user = account(identity_app, "canvas_switch")
    result = client.get("/api/v1/canvas-workspace", headers={"X-Canvas-Actor": "1"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "canvas_actor_changed"
    assert client.get("/api/v1/auth/me").json()["user"]["id"] == user["id"]
    assert (
        client.get("/api/v1/canvas-workspace", headers={"X-Canvas-Actor": user["id"]}).status_code
        == 200
    )


def test_viewport_compare_and_swap_does_not_modify_graph_or_overwrite_another_window(identity_app):
    client, user = account(identity_app, "canvas_viewport")
    created = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "viewport-project"},
        json={"name": "视口独立保存", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{created['id']}/canvases/{created['primary_canvas_id']}"
    document = client.get(path + "/my-document").json()
    original = document["source_document"]["viewport"]
    moved = {"x": 12.125, "y": -73.75, "k": 0.625}
    payload = {"expected_viewport": original, "viewport": moved}
    saved = client.put(path + "/viewport", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["viewport"] == moved
    assert client.put(path + "/viewport", json=payload).json() == saved.json()
    assert client.get(path).json()["row_version"] == document["row_version"]
    conflict = client.put(path + "/viewport", json={**payload, "viewport": {**moved, "x": 99}})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "canvas_viewport_conflict"
    # A graph saved from an older viewport must preserve the separately acknowledged position.
    stale_graph = document["source_document"]
    stale_graph["nodes"] = [
        {
            "id": "note",
            "type": "text",
            "title": "内容",
            "position": {"x": 0, "y": 0},
            "width": 300,
            "height": 200,
            "metadata": {"content": "保存正文"},
        }
    ]
    committed = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "viewport-graph"},
        json={"expected_row_version": document["row_version"], "source_document": stale_graph},
    )
    assert committed.status_code == 200, committed.text
    assert client.get(path + "/my-document").json()["source_document"]["viewport"] == moved


def test_viewport_animation_values_roundtrip_before_next_compare_and_swap(identity_app):
    client, _ = account(identity_app, "canvas_viewport_fraction")
    created = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "viewport-fraction-project"},
        json={"name": "动画视口往返", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{created['id']}/canvases/{created['primary_canvas_id']}"
    expected = client.get(path + "/my-document").json()["source_document"]["viewport"]
    for desired in (
        {"x": 163.8608222128106, "y": 364.21894629564247, "k": 1},
        {"x": 139.75, "y": 360.5, "k": 1},
        {"x": 0.10000000000000002, "y": -0.10000000000000002, "k": 0.3333333333333333},
        {"x": 16777216.99999999, "y": -163.8608222128106, "k": 1.0000000000000002},
    ):
        saved = client.put(
            path + "/viewport", json={"expected_viewport": expected, "viewport": desired}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["viewport"] == desired
        persisted = client.get(path + "/my-document").json()["source_document"]["viewport"]
        assert persisted == desired, (desired, persisted)
        expected = desired


def test_viewport_compare_and_swap_uses_mysql_json_float_representation(identity_app):
    client, _ = account(identity_app, "canvas_viewport_json_float")
    created = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "viewport-json-float-project"},
        json={"name": "视口 JSON 数值比较", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{created['id']}/canvases/{created['primary_canvas_id']}"
    origin = client.get(path + "/my-document").json()["source_document"]["viewport"]
    first = {"x": 139.30026109660574, "y": 453.20626631853787, "k": 0.720626631853786}
    payload = {"expected_viewport": origin, "viewport": first}
    saved = client.put(path + "/viewport", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["viewport"] == first
    # MySQL binary JSON's textual double representation differs by one ULP here.
    persisted = client.get(path + "/my-document").json()["source_document"]["viewport"]
    assert persisted == {**first, "y": 453.2062663185378}
    replayed = client.put(path + "/viewport", json=payload)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == saved.json()
    second = {"x": 176.5639686684073, "y": 455.53524804177545, "k": 0.5535248041775457}
    advanced = client.put(path + "/viewport", json={"expected_viewport": first, "viewport": second})
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["viewport"] == second
    assert int(advanced.json()["row_version"]) == int(saved.json()["row_version"]) + 1
    # A real move in another window still conflicts; no tolerance or automatic overwrite.
    conflict = client.put(
        path + "/viewport", json={"expected_viewport": first, "viewport": {**second, "x": 180}}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "canvas_viewport_conflict"
    final = client.get(path + "/my-document").json()["source_document"]
    assert final["viewport"] == second and final["revision"] == created["row_version"]


def test_view_preferences_persist_independently_and_stale_graph_cannot_reset_them(identity_app):
    client, _ = account(identity_app, "canvas_appearance")
    created = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "appearance-project"},
        json={"name": "外观独立保存", "workspace_mode": "infinite_canvas"},
    ).json()
    path = f"/api/v1/projects/{created['id']}/canvases/{created['primary_canvas_id']}"
    original = client.get(path + "/my-document").json()
    old = {"appearance": None, "backgroundMode": "dots", "showImageInfo": False}
    desired = {"appearance": {"mode": "light"}, "backgroundMode": "lines", "showImageInfo": True}
    payload = {"expected_preferences": old, "preferences": desired}
    saved = client.put(path + "/view-preferences", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["preferences"]["appearance"]["mode"] == "light"
    assert client.put(path + "/view-preferences", json=payload).json() == saved.json()
    assert client.get(path).json()["row_version"] == original["row_version"]
    assert client.get(path + "/revisions").json()["items"] == []
    stale = original["source_document"]
    stale["title"] = "旧请求的新正文标题"
    saved_graph = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "appearance-graph"},
        json={"expected_row_version": original["row_version"], "source_document": stale},
    )
    assert saved_graph.status_code == 200, saved_graph.text
    reread = client.get(path + "/my-document").json()["source_document"]
    assert {key: reread[key] for key in desired} == desired
    assert all(key not in client.get(path).json()["source_document"] for key in desired)
    conflict = client.put(path + "/view-preferences", json={**payload, "preferences": old})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "canvas_view_preferences_conflict"
    viewport = client.put(
        path + "/viewport",
        json={
            "expected_viewport": reread["viewport"],
            "viewport": {"x": 1.5, "y": 2, "k": 1.2},
        },
    )
    assert viewport.status_code == 200
    assert (
        client.put(
            path + "/view-preferences",
            json={
                "expected_preferences": desired,
                "preferences": {**desired, "backgroundMode": "blank"},
            },
        ).status_code
        == 200
    )
    for invalid in (
        {"appearance": {"mode": "custom"}},
        {"backgroundMode": "unknown"},
        {"appearance": {"mode": "dark", "apiKey": "not-a-secret"}},
    ):
        rejected = client.put(
            path + "/view-preferences",
            json={
                "expected_preferences": desired,
                "preferences": {**desired, **invalid},
            },
        )
        assert rejected.status_code == 422, rejected.text
