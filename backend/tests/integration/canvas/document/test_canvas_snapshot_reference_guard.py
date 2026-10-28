"""真实 MySQL 历史引用权限与查询增长回归；媒体正常场使用隔离 MinIO。"""

import inspect
import json
import os
import re
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update

from short_drama.core.exceptions import NotFound
from short_drama.core.identity import ActorContext
from short_drama.domain import CanvasBinaryResource, MediaFile, ProjectCanvas
from short_drama.domain.canvas.document.canvas import CanvasRevision, CanvasUserMediaReference
from short_drama.domain.canvas.resources.resource import CanvasUserBinaryReference
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.canvas import CanvasService
from short_drama.service.canvas.document.document import content_hash
from short_drama.utils.snowflake import next_id
from tests.integration.canvas.document.test_canvas_reference_writes import (
    reference_scene,
    saved_document,
)
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import (
    ROOT,
    account,
    canvas,
    png,
    upload,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="历史引用权限回归需显式启用隔离 MinIO",
    ),
]
KINDS = ("media", "binary")


def trusted_actor(user: dict[str, Any]) -> ActorContext:
    identifier = int(user["id"])
    return ActorContext(
        identifier,
        user["username"],
        user["email"],
        True,
        identifier,
        "test-csrf",
        f"snapshot-reference-{identifier}",
    )


def use_actor(session: Any, user: dict[str, Any], project_id: str) -> None:
    session.info["actor"] = trusted_actor(user)
    session.info["request_project"] = int(project_id)
    session.info["resource_scope"] = (None, int(project_id))


def audit(user_id: int) -> dict[str, Any]:
    now = utcnow()
    return {
        "id": next_id(),
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }


def reference_model(kind: str):
    return CanvasUserMediaReference if kind == "media" else CanvasUserBinaryReference


def private_reference(
    kind: str,
    project: dict[str, Any],
    user_id: int,
    revision_id: int,
    resource_id: int,
    slot: str,
):
    return reference_model(kind)(
        **audit(user_id),
        project_id=int(project["id"]),
        canvas_id=int(project["primary_canvas_id"]),
        user_id=user_id,
        revision_id=revision_id,
        owner_kind="revision",
        owner_key=str(revision_id),
        slot=slot,
        ordinal=0,
        **{"media_id" if kind == "media" else "binary_id": resource_id},
    )


def binary_upload(client: Any, key: str, source_key: str, value: int):
    body = b"glTF" + bytes([value]) * 64
    response = upload(
        client,
        body,
        key=key,
        source=source_key,
        kind="file",
        name="snapshot-model.glb",
        mime="model/gltf-binary",
    )
    assert response.status_code == 200, response.text
    return response.json()["resource"], body


def scene(resource_app: Any, kind: str, paths: int, label: str):
    if kind == "media":
        owner, project, path, document, shared, private = reference_scene(
            resource_app, paths, label
        )
        response = owner.get("/api/v1/auth/me")
        assert response.status_code == 200, response.text
        return (
            owner,
            response.json()["user"],
            project,
            path,
            document,
            shared,
            private,
            png("red"),
            png("blue"),
        )
    owner, user = account(resource_app, label)
    project, path, document = canvas(owner, label + "-project")
    shared, shared_body = binary_upload(owner, label + "-shared", document["id"], 1)
    private, private_body = binary_upload(owner, label + "-private", document["id"], 2)
    document["nodes"] = [
        {
            "id": "prose",
            "type": "text",
            "title": "文字",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"content": "保存前正文"},
        }
    ]
    document["directorScenes"] = [
        {
            "id": "guard-scene",
            "model": f"resource:{shared['id']}",
            "taskConfig": {"referenceResourceIds": [private["id"]] * paths},
        }
    ]
    return (
        owner,
        user,
        project,
        path,
        saved_document(owner, path, document, label + "-seed"),
        shared,
        private,
        shared_body,
        private_body,
    )


def reference_count(factory: Any, kind: str, canvas_id: int) -> int:
    model = reference_model(kind)
    with factory() as session:
        return session.scalar(
            select(func.count()).select_from(model).where(model.canvas_id == canvas_id)
        )


class GuardSelects:
    """只记录真实 guard 查询元数据，不保存 SQL 文本、参数或局部变量。"""

    def __init__(self) -> None:
        self.in_snapshot = False
        self.tables: Counter[str] = Counter()
        self.current_resource_reads = 0

    def collect(self, _connection, _cursor, statement, _parameters, _context, _many):
        if not self.in_snapshot or not statement.lstrip().upper().startswith("SELECT"):
            return
        frame = inspect.currentframe()
        in_guard = False
        try:
            while frame is not None:
                if frame.f_code.co_name == "guard_writes" and frame.f_code.co_filename.replace(
                    "\\", "/"
                ).endswith("/db/access.py"):
                    in_guard = True
                    break
                frame = frame.f_back
        finally:
            del frame
        if not in_guard:
            return
        match = re.search(r"\bFROM\s+`?([a-z_][a-z0-9_]*)`?", statement, re.I)
        assert match is not None
        table = match[1].lower()
        self.tables[table] += 1
        if table in {"media_files", "canvas_binary_resources"} and re.search(
            r"\b(?:FOR SHARE|LOCK IN SHARE MODE)\b", statement, re.I
        ):
            self.current_resource_reads += 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("paths", [2, 100])
def test_due_snapshot_keeps_each_private_path_without_per_path_guard_queries(
    resource_app: Any,
    database_errors: list,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    paths: int,
) -> None:
    label = f"snapguard_{kind}_{paths}"
    owner, user, project, path, document, shared, private, shared_body, private_body = scene(
        resource_app, kind, paths, label
    )
    outsider, _ = account(resource_app, label + "_other")
    factory = resource_app[1]
    engine = factory.kw["bind"]
    assert (engine.url.database or "").endswith("_test")
    canvas_id = int(project["primary_canvas_id"])
    before = deepcopy(document)
    original_snapshot = CanvasService._snapshot
    measurements = GuardSelects()
    report = {
        "status": "failed",
        "scope": "真实 HTTP/MySQL/MinIO；单次到期快照与 SQL 增长门禁，非 p95 或供应商验收",
        "kind": kind,
        "private_paths": paths,
        "distinct_resources": 2,
        "source_sha256": sha256(
            (Path(__file__).resolve().parents[4] / "src/short_drama/db/access.py").read_bytes()
        ).hexdigest(),
    }

    def observe_snapshot(service, canvas_row, previous, *, reason):
        measurements.in_snapshot = True
        try:
            return original_snapshot(service, canvas_row, previous, reason=reason)
        finally:
            measurements.in_snapshot = False

    monkeypatch.setattr(CanvasService, "_snapshot", observe_snapshot)
    try:
        with engine.begin() as connection:
            connection.execute(
                update(CanvasRevision.__table__)
                .where(CanvasRevision.canvas_id == canvas_id)
                .values(created_at=utcnow() - timedelta(minutes=6))
            )
        document["nodes"][0]["metadata"]["content"] = "到期保存后的正文"
        event.listen(engine, "before_cursor_execute", measurements.collect)
        try:
            response = owner.post(
                path + "/commits",
                headers={"Idempotency-Key": label + "-due"},
                json={"expected_row_version": document["revision"], "source_document": document},
            )
        finally:
            event.remove(engine, "before_cursor_execute", measurements.collect)
        report["response_status"] = response.status_code
        report["guard_selects_by_primary_table"] = dict(measurements.tables)
        report["current_resource_reads"] = measurements.current_resource_reads
        assert response.status_code == 200, response.text
        assert int(response.json()["row_version"]) == int(before["revision"]) + 1
        history = owner.get(path + "/revisions").json()["items"]
        assert len(history) == 2
        revision_id = int(history[0]["id"])
        model = reference_model(kind)
        with factory() as session:
            rows = session.scalars(select(model).where(model.revision_id == revision_id)).all()
            assert len(rows) == paths, "每个私人路径须独立冻结，不能按资源身份合并"
            assert len({row.slot for row in rows}) == paths
            assert {row.user_id for row in rows} == {int(user["id"])}
            assert {row.owner_key for row in rows} == {str(revision_id)}
            assert {
                getattr(row, "media_id" if kind == "media" else "binary_id") for row in rows
            } == {int(private["id"])}
        frozen = owner.get(path + f"/revisions/{revision_id}").json()["source_document"]
        assert frozen["nodes"] == before["nodes"]
        if kind == "binary":
            assert frozen["directorScenes"] == before["directorScenes"]
        assert owner.get(f"{ROOT}/{private['id']}/file").content == private_body
        assert owner.get(f"{ROOT}/{shared['id']}/file").content == shared_body
        assert outsider.get(f"{ROOT}/{private['id']}/file").status_code == 404
        assert outsider.get(path + f"/revisions/{revision_id}").status_code == 404
        assert database_errors == []
        report["functional_postconditions"] = "passed"
        assert measurements.current_resource_reads > 0, "必须实际执行 guard 资源当前读"
        assert sum(measurements.tables.values()) <= 24, (
            "同一项目/画布/两资源的快照 guard 不得逐私人路径重复读取父表或资源："
            f"{dict(measurements.tables)}"
        )
        report["status"] = "passed"
    finally:
        owner.close()
        outsider.close()
        (tmp_path / f"snapshot-reference-guard-{kind}-{paths}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("invalid", ["wrong_author", "foreign_project", "foreign_canvas", "hidden"])
def test_new_revision_reference_rejects_invalid_scope_and_rolls_back_all_rows(
    resource_app: Any, database_errors: list, kind: str, invalid: str
) -> None:
    label = f"sgdeny_{kind}_{invalid.removeprefix('foreign_').removeprefix('wrong_')}"
    owner, user, project, path, document, _, private, _, _ = scene(resource_app, kind, 2, label)
    member, member_user = account(resource_app, label + "_member")
    factory = resource_app[1]
    model = reference_model(kind)
    canvas_id = int(project["primary_canvas_id"])
    revision_id = int(owner.get(path + "/revisions").json()["items"][0]["id"])
    initial_count = reference_count(factory, kind, canvas_id)
    try:
        invalid_resource = int(private["id"])
        foreign_canvas_id = None
        if invalid in {"foreign_project", "foreign_canvas"}:
            foreign_project, _, foreign_document = canvas(owner, label + "-foreign")
            if kind == "media":
                response = upload(
                    owner, png("green"), key=label + "-file", source=foreign_document["id"]
                )
                assert response.status_code == 200, response.text
                invalid_resource = int(response.json()["resource"]["id"])
            else:
                resource, _ = binary_upload(owner, label + "-file", foreign_document["id"], 3)
                invalid_resource = int(resource["id"])
            if invalid == "foreign_canvas":
                foreign_canvas_id = int(foreign_project["primary_canvas_id"])
                assert foreign_canvas_id != canvas_id
        elif invalid == "hidden":
            _, _, hidden_document = canvas(member, label + "-private-owner")
            if kind == "media":
                response = upload(
                    member, png("green"), key=label + "-file", source=hidden_document["id"]
                )
                assert response.status_code == 200, response.text
                invalid_resource = int(response.json()["resource"]["id"])
            else:
                resource, _ = binary_upload(member, label + "-file", hidden_document["id"], 3)
                invalid_resource = int(resource["id"])
            assert owner.get(f"{ROOT}/{invalid_resource}/file").status_code == 404
        first = private_reference(
            kind, project, int(user["id"]), revision_id, int(private["id"]), "good"
        )
        denied = private_reference(
            kind, project, int(user["id"]), revision_id, invalid_resource, "denied"
        )
        if invalid == "wrong_author":
            denied.user_id = int(member_user["id"])
        elif invalid == "foreign_canvas":
            denied.canvas_id = foreign_canvas_id
        with factory() as session:
            use_actor(session, user, project["id"])
            with pytest.raises(NotFound), session.begin():
                session.add_all([first, denied])
                session.flush()
        assert reference_count(factory, kind, canvas_id) == initial_count
        with factory() as session:
            assert (
                session.scalar(select(model.id).where(model.id.in_([first.id, denied.id]))) is None
            )
        assert owner.get(path + "/my-document").json()["source_document"] == document
        assert database_errors == []
    finally:
        owner.close()
        member.close()


def guard_only_resource(kind: str, user_id: int, project_id: int | None = None):
    """仅用于 ORM 权限/生命周期；正常场另验真实上传字节，不宣称本资源已归档。"""
    values = {
        **audit(user_id),
        "scope_user_id": None if project_id else user_id,
        "project_id": project_id,
        "published_at": None,
        "storage_locator": f"guard-test/{uuid4().hex}",
        "original_name": "guard-test.png" if kind == "media" else "guard-test.glb",
        "byte_size": 68,
        "checksum_sha256": "0" * 64,
    }
    if kind == "media":
        return MediaFile(**values, format_code="image/png", width=37, height=19)
    return CanvasBinaryResource(**values, resource_kind="file", mime_type="model/gltf-binary")


@pytest.mark.parametrize("kind", KINDS)
def test_later_flush_rechecks_deleted_resource_and_rolls_back_prior_reference(
    resource_app: Any, database_errors: list, kind: str
) -> None:
    label = f"snapdelete_{kind}"
    owner, user, project, path, _, _, _, _, _ = scene(resource_app, kind, 2, label)
    factory = resource_app[1]
    canvas_id = int(project["primary_canvas_id"])
    initial_count = reference_count(factory, kind, canvas_id)
    revision_id = int(owner.get(path + "/revisions").json()["items"][0]["id"])
    resource = guard_only_resource(kind, int(user["id"]))
    try:
        with factory() as session:
            use_actor(session, user, project["id"])
            with pytest.raises(NotFound), session.begin():
                session.add(resource)
                session.flush()
                first = private_reference(
                    kind, project, int(user["id"]), revision_id, resource.id, "first"
                )
                session.add(first)
                session.flush()
                session.delete(first)
                session.flush()
                session.delete(resource)
                session.flush()
                stale = private_reference(
                    kind, project, int(user["id"]), revision_id, resource.id, "stale"
                )
                session.add(stale)
                session.flush()
        assert reference_count(factory, kind, canvas_id) == initial_count
        with factory() as session:
            assert (
                session.scalar(select(type(resource).id).where(type(resource).id == resource.id))
                is None
            )
        assert database_errors == []
    finally:
        owner.close()


@pytest.mark.parametrize("kind", KINDS)
def test_later_flush_with_changed_actor_cannot_reuse_another_authors_resource_read(
    resource_app: Any, database_errors: list, kind: str
) -> None:
    label = f"snapactor_{kind}"
    owner, user, project, path, _, _, private, _, _ = scene(resource_app, kind, 2, label)
    member, member_user = account(resource_app, label + "_member")
    factory = resource_app[1]
    canvas_id = int(project["primary_canvas_id"])
    initial_count = reference_count(factory, kind, canvas_id)
    revision_id = int(owner.get(path + "/revisions").json()["items"][0]["id"])
    try:
        assert member.get(f"{ROOT}/{private['id']}/file").status_code == 404
        with factory() as session:
            use_actor(session, user, project["id"])
            with pytest.raises(NotFound), session.begin():
                session.add(
                    private_reference(
                        kind, project, int(user["id"]), revision_id, int(private["id"]), "owner"
                    )
                )
                session.flush()
                use_actor(session, member_user, project["id"])
                session.add(
                    private_reference(
                        kind,
                        project,
                        int(member_user["id"]),
                        revision_id,
                        int(private["id"]),
                        "member",
                    )
                )
                session.flush()
        assert reference_count(factory, kind, canvas_id) == initial_count
        assert database_errors == []
    finally:
        owner.close()
        member.close()


@pytest.mark.parametrize("kind", KINDS)
def test_later_transaction_rechecks_archived_canvas_without_reusing_previous_flush(
    resource_app: Any, database_errors: list, kind: str
) -> None:
    label = f"snaparchive_{kind}"
    owner, user, project, path, document, shared, _, _, _ = scene(resource_app, kind, 2, label)
    factory = resource_app[1]
    canvas_id = int(project["primary_canvas_id"])
    initial_count = reference_count(factory, kind, canvas_id)
    revision_id = int(owner.get(path + "/revisions").json()["items"][0]["id"])
    try:
        with factory() as session:
            use_actor(session, user, project["id"])
            with session.begin():
                session.add(
                    private_reference(
                        kind,
                        project,
                        int(user["id"]),
                        revision_id,
                        int(shared["id"]),
                        "before-archive",
                    )
                )
                session.flush()
            response = owner.request(
                "DELETE",
                path,
                headers={"Idempotency-Key": label + "-archive"},
                json={"expected_row_version": document["revision"]},
            )
            assert response.status_code == 200, response.text
            with pytest.raises(NotFound), session.begin():
                session.add(
                    private_reference(
                        kind,
                        project,
                        int(user["id"]),
                        revision_id,
                        int(shared["id"]),
                        "after-archive",
                    )
                )
                session.flush()
        assert reference_count(factory, kind, canvas_id) == initial_count + 1
        assert owner.get(path + f"/revisions/{revision_id}").status_code == 404
        assert database_errors == []
    finally:
        owner.close()


@pytest.mark.parametrize("kind", KINDS)
def test_pending_canvas_and_resource_still_receive_checked_reference_writes(
    resource_app: Any, database_errors: list, kind: str
) -> None:
    owner, user = account(resource_app, f"snappending_{kind}")
    project, _, document = canvas(owner, f"snappending-{kind}-project")
    factory = resource_app[1]
    user_id = int(user["id"])
    parent = ProjectCanvas(
        **audit(user_id),
        project_id=int(project["id"]),
        source_key=uuid4().hex,
        title="待写入父画布",
        position=1,
        schema_version=1,
        row_version=1,
        properties_json={},
        archived_at=None,
    )
    snapshot = deepcopy(document)
    snapshot["id"] = parent.source_key
    revision = CanvasRevision(
        **audit(user_id),
        project_id=int(project["id"]),
        canvas_id=parent.id,
        canvas_row_version=1,
        schema_version=1,
        snapshot_json=snapshot,
        content_hash=content_hash(snapshot),
        reason="automatic",
    )
    resource = guard_only_resource(kind, user_id)
    target_project = {**project, "primary_canvas_id": str(parent.id)}
    references = [
        private_reference(
            kind, target_project, user_id, revision.id, resource.id, f"pending-{index}"
        )
        for index in range(2)
    ]
    try:
        with factory() as session:
            use_actor(session, user, project["id"])
            with session.begin():
                session.add_all([parent, revision, resource, *references])
                # before_flush 仍检查全部 session.new；限制实际 INSERT 的父子顺序。
                session.flush([parent])
                session.flush([revision])
                session.flush([resource])
                session.flush()
        assert reference_count(factory, kind, parent.id) == 2
        with factory() as session:
            assert (
                session.scalar(select(type(resource).id).where(type(resource).id == resource.id))
                == resource.id
            )
            rows = session.scalars(
                select(reference_model(kind)).where(reference_model(kind).canvas_id == parent.id)
            ).all()
            assert {row.slot for row in rows} == {"pending-0", "pending-1"}
        assert database_errors == []
    finally:
        owner.close()


def guarded_identity_scene(identity_app: Any, label: str):
    """使用真实账号和作品保存准备父表，不创建存储桶或伪造身份字段。"""
    owner, user = account(identity_app, label)
    project, path, document = canvas(owner, label + "-project")
    document["nodes"] = [
        {
            "id": "prose",
            "type": "text",
            "title": "正文",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"content": "权限回归父文档"},
        }
    ]
    document = saved_document(owner, path, document, label + "-seed")
    response = owner.get(path + "/revisions")
    assert response.status_code == 200, response.text
    revision_id = int(response.json()["items"][0]["id"])
    return owner, user, project, path, document, revision_id


def measured_guard_flush(session: Any, measurements: GuardSelects) -> None:
    """仅观察本次真实 ORM flush，查询元数据明确属于 guard 而非快照时延。"""
    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", measurements.collect)
    measurements.in_snapshot = True
    try:
        session.flush()
    finally:
        measurements.in_snapshot = False
        event.remove(engine, "before_cursor_execute", measurements.collect)


def stored_references(factory: Any, kind: str, canvas_id: int, slots: set[str]):
    model = reference_model(kind)
    with factory() as session:
        return session.scalars(
            select(model).where(model.canvas_id == canvas_id, model.slot.in_(slots))
        ).all()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("resource_scope", ["personal", "project"])
def test_guard_current_read_sees_committed_resource_after_earlier_repeatable_read_snapshot(
    identity_app: Any,
    database_errors: list,
    tmp_path: Path,
    kind: str,
    resource_scope: str,
) -> None:
    label = f"sgrr_{kind}_{resource_scope}"
    owner, user, project, path, document, revision_id = guarded_identity_scene(identity_app, label)
    factory = identity_app[1]
    canvas_id = int(project["primary_canvas_id"])
    user_id = int(user["id"])
    resource = guard_only_resource(
        kind, user_id, int(project["id"]) if resource_scope == "project" else None
    )
    model = type(resource)
    reference = private_reference(kind, project, user_id, revision_id, resource.id, "after-commit")
    measurements = GuardSelects()
    report = {
        "status": "failed",
        "scope": "真实 Actor ORM 与 MySQL RR/current read；资源仅验权限，非媒体归档或供应商验收",
        "kind": kind,
        "resource_scope": resource_scope,
    }
    try:
        with factory() as first:
            use_actor(first, user, project["id"])
            with first.begin():
                assert first.connection().get_isolation_level() == "REPEATABLE READ"
                # 普通一致性读取建立快照，尚未取得父项目排他锁。
                assert (
                    first.scalar(select(ProjectCanvas.id).where(ProjectCanvas.id == canvas_id))
                    == canvas_id
                )
                assert first.scalar(select(model.id).where(model.id == resource.id)) is None
                with factory() as publisher:
                    use_actor(publisher, user, project["id"])
                    with publisher.begin():
                        publisher.add(resource)
                        publisher.flush()
                # 保留同一事务：资源虽已提交，普通读取仍只能看见原 RR 快照。
                assert first.scalar(select(model.id).where(model.id == resource.id)) is None
                report["ordinary_read_after_commit"] = "absent"
                first.add(reference)
                measured_guard_flush(first, measurements)
        report["guard_selects_by_primary_table"] = dict(measurements.tables)
        report["current_resource_reads"] = measurements.current_resource_reads
        assert measurements.current_resource_reads > 0
        rows = stored_references(factory, kind, canvas_id, {"after-commit"})
        assert len(rows) == 1 and rows[0].id == reference.id
        assert getattr(rows[0], "media_id" if kind == "media" else "binary_id") == resource.id
        assert rows[0].user_id == user_id
        assert owner.get(path + "/my-document").json()["source_document"] == document
        assert database_errors == []
        report["status"] = "passed"
    finally:
        owner.close()
        (tmp_path / f"snapshot-reference-rr-{kind}-{resource_scope}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("missing_tail", [False, True], ids=["valid", "missing-tail"])
def test_guard_501_distinct_resources_checks_the_final_chunk_and_rolls_back_missing_tail(
    identity_app: Any,
    database_errors: list,
    tmp_path: Path,
    kind: str,
    missing_tail: bool,
) -> None:
    label = f"sg501_{kind}_{'missing' if missing_tail else 'valid'}"
    owner, user, project, path, document, revision_id = guarded_identity_scene(identity_app, label)
    factory = identity_app[1]
    user_id = int(user["id"])
    canvas_id = int(project["primary_canvas_id"])
    initial_count = reference_count(factory, kind, canvas_id)
    # 资源本身私人且无引用；准备成本不混入正式引用 flush 的 SQL 记录。
    resources = [guard_only_resource(kind, user_id) for _ in range(500 if missing_tail else 501)]
    resource_ids = [resource.id for resource in resources]
    if missing_tail:
        resource_ids.append(next_id())
    assert len(resource_ids) == len(set(resource_ids)) == 501
    references = [
        private_reference(kind, project, user_id, revision_id, identifier, f"chunk-{index}")
        for index, identifier in enumerate(resource_ids)
    ]
    slots = {row.slot for row in references}
    measurements = GuardSelects()
    report = {
        "status": "failed",
        "scope": "真实 Actor ORM/MySQL 的 501 身份分片边界；资源仅验权限，不声明实际媒体归档",
        "kind": kind,
        "reference_paths": 501,
        "distinct_resource_ids": 501,
        "missing_tail": missing_tail,
    }
    try:
        with factory() as creator:
            use_actor(creator, user, project["id"])
            with creator.begin():
                creator.add_all(resources)
                creator.flush()
        with factory() as writer:
            use_actor(writer, user, project["id"])
            if missing_tail:
                with pytest.raises(NotFound), writer.begin():
                    writer.add_all(references)
                    measured_guard_flush(writer, measurements)
            else:
                with writer.begin():
                    writer.add_all(references)
                    measured_guard_flush(writer, measurements)
        report["guard_selects_by_primary_table"] = dict(measurements.tables)
        report["current_resource_reads"] = measurements.current_resource_reads
        assert measurements.current_resource_reads == 2, "501 独立身份须完整读取 500+1 两批当前行"
        rows = stored_references(factory, kind, canvas_id, slots)
        if missing_tail:
            assert rows == [], "缺失尾项须在 guard 拒绝，不能先插入前 500 条再留下部分成功"
            assert reference_count(factory, kind, canvas_id) == initial_count
        else:
            assert len(rows) == 501 and {row.slot for row in rows} == slots
            assert {row.id for row in rows} == {reference.id for reference in references}
            assert {
                getattr(row, "media_id" if kind == "media" else "binary_id") for row in rows
            } == set(resource_ids)
            assert {row.user_id for row in rows} == {user_id}
            assert {row.revision_id for row in rows} == {revision_id}
            assert reference_count(factory, kind, canvas_id) == initial_count + 501
        assert owner.get(path + "/my-document").json()["source_document"] == document
        assert database_errors == []
        report["status"] = "passed"
    finally:
        owner.close()
        (
            tmp_path
            / f"snapshot-reference-chunk-{kind}-{'missing' if missing_tail else 'valid'}.json"
        ).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["dirty", "deleted"])
def test_mixed_resource_changes_keep_checked_writes_when_snapshot_batching_falls_back(
    identity_app: Any, database_errors: list, kind: str, change: str
) -> None:
    label = f"sgmix_{kind}_{change}"
    owner, user, project, path, document, revision_id = guarded_identity_scene(identity_app, label)
    factory = identity_app[1]
    user_id = int(user["id"])
    canvas_id = int(project["primary_canvas_id"])
    changed = guard_only_resource(kind, user_id)
    target = changed if change == "dirty" else guard_only_resource(kind, user_id)
    model = type(target)
    slots = {"mixed-0", "mixed-1"}
    references = [
        private_reference(kind, project, user_id, revision_id, target.id, slot)
        for slot in sorted(slots)
    ]
    measurements = GuardSelects()
    try:
        with factory() as creator:
            use_actor(creator, user, project["id"])
            with creator.begin():
                creator.add_all([changed] if changed is target else [changed, target])
                creator.flush()
        with factory() as writer:
            use_actor(writer, user, project["id"])
            with writer.begin():
                current = writer.scalar(select(model).where(model.id == changed.id))
                assert current is not None
                if change == "dirty":
                    current.original_name = (
                        "checked-renamed.png" if kind == "media" else "checked-renamed.glb"
                    )
                else:
                    # 删除的资源没有引用；新增引用指向另一仍然有效的资源。
                    writer.delete(current)
                writer.add_all(references)
                measured_guard_flush(writer, measurements)
        assert measurements.current_resource_reads > 0
        rows = stored_references(factory, kind, canvas_id, slots)
        assert len(rows) == 2 and {row.slot for row in rows} == slots
        assert {getattr(row, "media_id" if kind == "media" else "binary_id") for row in rows} == {
            target.id
        }
        with factory() as reader:
            current = reader.scalar(select(model).where(model.id == changed.id))
            if change == "dirty":
                assert current is not None
                assert current.original_name == (
                    "checked-renamed.png" if kind == "media" else "checked-renamed.glb"
                )
            else:
                assert current is None
            assert reader.scalar(select(model.id).where(model.id == target.id)) == target.id
        assert owner.get(path + "/my-document").json()["source_document"] == document
        assert database_errors == []
    finally:
        owner.close()
