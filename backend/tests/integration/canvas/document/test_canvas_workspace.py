"""Real MySQL verifies atomicity, access scope, CAS and source-document roundtrips."""

import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import Project, ProjectCanvas, ProjectCanvasSettings
from short_drama.domain.canvas.document.canvas import (
    CanvasEdge,
    CanvasNode,
    CanvasNodeUserState,
    CanvasRevision,
    CanvasWriteReceipt,
)
from short_drama.domain.identity.accounts.user import User
from short_drama.domain.identity.audit.models import AuditEvent
from short_drama.schemas.canvas.document.canvas import CanvasCreateRequest
from short_drama.schemas.canvas.workspace.workspace import CanvasWorkspacePreferencesRequest
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.canvas import CanvasService
from short_drama.service.canvas.workspace.workspace import CanvasWorkspaceService
from short_drama.service.projects.management.project import ProjectService

pytestmark = pytest.mark.integration


def test_workspace_batches_paged_documents_and_keeps_private_projection_scoped(db_session):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    service.commit(project_id, canvas_id, edit(service, project_id, canvas_id), "page-edit")
    for index in range(4):
        service.create_for_project(project_id, {"title": f"额外画布 {index}"}, f"page-{index}")
    statements = []

    def collect(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", collect)
    try:
        page = service.list_workspace(page=1, page_size=5, include_documents=True)
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert page["total"] == 5 and len(page["items"]) == 5 and not page["has_more"]
    assert len(statements) <= 10, "list must batch relational rows rather than query per canvas"
    author = next(item for item in page["items"] if item["id"] == str(canvas_id))
    assert author["source_document"]["nodes"][0]["metadata"]["prompt"] == "作者私人提示词"
    assert service.list_workspace(page=2, page_size=2)["has_more"]
    assert len(service.list_workspace(page=3, page_size=2)["items"]) == 1
    assert service.list_workspace(query="额外画布")["total"] == 4
    assert service.list_workspace(query="%")["total"] == 0
    add_user(db_session)
    db_session.info["actor"] = actor(2)
    assert service.list_workspace(include_documents=True)["total"] == 0
    with pytest.raises(NotFound):
        service.read(project_id, canvas_id, private=True)
    db_session.info["actor"] = actor(999)
    assert service.list_workspace()["total"] == 0


def actor(user_id=1):
    return ActorContext(
        user_id,
        f"canvas{user_id}",
        f"canvas{user_id}@example.test",
        True,
        user_id,
        "csrf",
        f"canvas-test-{user_id}",
    )


def create(session, key="create-one"):
    session.info["actor"] = actor()
    project = ProjectService(session).create_project(
        {"name": "无限画布", "workspace_mode": "infinite_canvas"},
        idempotency_key=key,
    )
    return project.id, project.primary_canvas_id


def node(key, *, parent=None):
    value = {
        "id": key,
        "type": "text",
        "title": "正文",
        "position": {"x": -0.125, "y": 812.375},
        "width": 301.75,
        "height": 180,
        "metadata": {
            "content": "共同作品",
            "prompt": "作者私人提示词",
            "taskId": "999999999999999999",
            "model": "private-model",
            "errorDetails": "仅作者可见",
        },
    }
    if parent:
        value["parentId"] = parent
    return value


def edit(service, project_id, canvas_id):
    document = service.read(project_id, canvas_id, private=True)["source_document"]
    document["nodes"] = [node("a"), node("b", parent="a")]
    document["connections"] = [
        {"id": "edge", "fromNodeId": "a", "toNodeId": "b", "fromHandleId": "out"}
    ]
    document["chatSessions"] = [{"id": "private-chat", "messages": [{"content": "作者的对话"}]}]
    return {"expected_row_version": document["revision"], "source_document": document}


def add_user(session, user_id=2):
    with session.begin():
        session.add(
            User(
                id=user_id,
                username=f"member{user_id}",
                display_name="独立用户",
                email=f"member{user_id}@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )


def test_create_mode_default_atomic_receipt_and_rollback(db_session, monkeypatch):
    session = db_session
    session.info["actor"] = actor()
    standard = ProjectService(session).create_project({"name": "标准"})
    assert standard.workspace_mode == "standard" and standard.primary_canvas_id is None
    project_id, canvas_id = create(session)
    assert create(session) == (project_id, canvas_id)
    with session.begin():
        assert session.scalar(select(func.count()).select_from(ProjectCanvas)) == 1
        assert session.scalar(select(func.count()).select_from(CanvasWriteReceipt)) == 1
        assert session.scalar(select(ProjectCanvasSettings.primary_canvas_id)) == canvas_id
    with pytest.raises(WorkflowError) as error:
        ProjectService(session).create_project(
            {"name": "不同内容", "workspace_mode": "infinite_canvas"},
            idempotency_key="create-one",
        )
    assert error.value.code == "canvas_idempotency_conflict"
    original = CanvasService.initialize_project

    def fail_after_primary(self, project):
        original(self, project)
        raise RuntimeError("injected after primary canvas flush")

    monkeypatch.setattr(CanvasService, "initialize_project", fail_after_primary)
    with pytest.raises(RuntimeError, match="injected"):
        create(session, "rollback")
    with session.begin():
        assert session.scalar(select(func.count()).select_from(Project)) == 2
        assert session.scalar(select(func.count()).select_from(ProjectCanvas)) == 1


def test_source_new_workspace_preserves_graph_and_replays_atomically(db_session):
    session = db_session
    session.info["actor"] = actor()
    source_key = "source-new-canvas"
    request = CanvasCreateRequest(
        title="源新建项目",
        source_key=source_key,
        source_document={
            "id": source_key,
            "workspaceProjectId": source_key,
            "revision": "0",
            "title": "源新建项目",
            "nodes": [node("first")],
            "connections": [],
        },
    )
    service = CanvasService(session)
    saved = service.create_workspace(request, "source-workspace-create")
    assert saved == service.create_workspace(request, "source-workspace-create")
    read = service.read(int(saved["project_id"]), int(saved["id"]), private=True)
    assert read["source_document"]["nodes"] == [node("first")]
    assert read["source_document"]["workspaceProjectId"] == saved["project_id"]
    with session.begin():
        assert session.scalar(select(func.count()).select_from(Project)) == 1
        assert session.scalar(select(func.count()).select_from(ProjectCanvas)) == 1


def test_workspace_model_preferences_are_private_and_never_force_conflicts(db_session):
    session = db_session
    project_id, _ = create(session)
    add_user(session)
    service = CanvasWorkspaceService(session)
    assert service.read_models()["row_version"] == "0"
    request = CanvasWorkspacePreferencesRequest(
        expected_row_version="0", preferences={"systemPrompt": "私人生成指令", "size": "16:9"}
    )
    assert service.save_preferences(request)["row_version"] == "1"
    with pytest.raises(WorkflowError) as error:
        service.save_preferences(request)
    assert error.value.code == "canvas_model_preferences_conflict"
    with Session(session.bind, expire_on_commit=False, autoflush=False) as other:
        other.info["actor"] = actor(2)
        assert CanvasWorkspaceService(other).read_models()["preferences"] == {}
    with session.begin():
        events = session.scalars(select(AuditEvent)).all()
        assert all("私人生成指令" not in repr(value.__dict__) for value in events)


def test_saved_legacy_modelhub_capabilities_reach_workspace_without_mutating_config(db_session):
    from short_drama.domain import AIModelConfig
    from short_drama.schemas.ai_config.management.models import AIModelConfigCreate
    from short_drama.schemas.canvas.workspace.workspace import CanvasWorkspaceModelsRead
    from short_drama.service.ai_config.management.models import AIModelConfigService

    create(db_session)
    saved = AIModelConfigService(db_session).create(
        AIModelConfigCreate(
            name="普通 ModelHub 视频",
            model_key="seedance-2.0-mini",
            provider="modelhub",
            service_type="video",
            base_url="https://api.modelhub.cc",
        ).model_dump()
    )
    public = CanvasWorkspaceModelsRead.model_validate(
        CanvasWorkspaceService(db_session).read_models()
    ).model_dump(mode="json")
    projected = next(model for model in public["models"] if model["id"] == str(saved.id))
    assert projected["runtime_profile"] is None
    assert "reference_to_video" in projected["default_capability_config"]["video"]["operations"]
    with db_session.begin():
        stored = db_session.get(AIModelConfig, int(saved.id))
        assert stored.runtime_profile is None
        assert stored.row_version == int(saved.row_version)


def edge_rows(session, canvas_id):
    with session.begin():
        return {
            row.edge_key: {
                column.key: deepcopy(getattr(row, column.key))
                for column in CanvasEdge.__table__.columns
            }
            for row in session.scalars(select(CanvasEdge).where(CanvasEdge.canvas_id == canvas_id))
        }


@pytest.mark.parametrize("node_count", [4, 100])
def test_text_edit_does_not_write_unchanged_edges(db_session, node_count):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    document = service.read(project_id, canvas_id, private=True)["source_document"]
    document["nodes"] = [node(f"node-{index}") for index in range(node_count)]
    document["connections"] = [
        {
            "id": f"edge-{index}",
            "fromNodeId": f"node-{index}",
            "toNodeId": f"node-{index + 1}",
        }
        for index in range(0, node_count, 2)
    ]
    seeded = service.commit(
        project_id,
        canvas_id,
        {"expected_row_version": document["revision"], "source_document": document},
        "edges-seed",
    )
    before = edge_rows(db_session, canvas_id)
    document["revision"] = seeded["row_version"]
    document["nodes"][0]["metadata"]["content"] = "仅修改一个文字节点"
    updated_edges = 0

    def collect(_connection, _cursor, statement, parameters, _context, many):
        nonlocal updated_edges
        if re.match(r"\s*UPDATE `?canvas_edges`?\s", statement, re.I):
            updated_edges += len(parameters) if many else 1

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", collect)
    try:
        saved = service.commit(
            project_id,
            canvas_id,
            {"expected_row_version": document["revision"], "source_document": document},
            "edges-text-edit",
        )
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert saved["row_version"] == str(int(seeded["row_version"]) + 1)
    assert updated_edges == 0, "文字修改不能重写所有未变连线或推进其更新时间"
    assert edge_rows(db_session, canvas_id) == before
    current = service.read(project_id, canvas_id, private=True)["source_document"]
    assert current["nodes"] == document["nodes"]
    assert current["connections"] == document["connections"]


def test_changed_edges_keep_fields_order_archive_and_stable_revival(db_session):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    document = service.read(project_id, canvas_id, private=True)["source_document"]
    document["nodes"] = [node(key) for key in ("a", "b", "c", "d")]
    document["connections"] = [
        {"id": "first", "fromNodeId": "a", "toNodeId": "b"},
        {"id": "second", "fromNodeId": "c", "toNodeId": "d"},
    ]

    def save(key):
        result = service.commit(
            project_id,
            canvas_id,
            {"expected_row_version": document["revision"], "source_document": document},
            key,
        )
        document["revision"] = result["row_version"]
        current = service.read(project_id, canvas_id, private=True)["source_document"]
        assert current["connections"] == document["connections"]
        return edge_rows(db_session, canvas_id)

    initial = save("edge-fields-seed")
    for field, value in (
        ("fromNodeId", "c"),
        ("toNodeId", "d"),
        ("fromHandleId", "output"),
        ("toHandleId", "input"),
        ("relation", "batch-output"),
        ("storyboardRowId", "row-1"),
        ("pathD", "M 0 0 L 10 10"),
    ):
        previous = edge_rows(db_session, canvas_id)
        document["connections"][0][field] = value
        changed = save(f"edge-field-{field}")
        assert changed["first"]["id"] == initial["first"]["id"]
        assert changed["first"]["updated_at"] > previous["first"]["updated_at"]
        assert changed["second"] == previous["second"]
    document["connections"].reverse()
    reordered = save("edges-reorder")
    assert reordered["second"]["position"] == 0 and reordered["first"]["position"] == 1
    removed = document["connections"].pop()
    archived = save("edges-delete")
    assert archived["first"]["archived_at"] is not None
    document["nodes"][0]["metadata"]["content"] = "删除连线后的文字修改"
    retained = save("edges-after-delete")
    assert retained == archived, "后续保存必须保留原归档时间且不重写其余未变连线"
    document["connections"].append(removed)
    revived = save("edges-revive")
    assert revived["first"]["id"] == initial["first"]["id"]
    assert revived["first"]["archived_at"] is None
    assert revived["first"]["updated_at"] > archived["first"]["updated_at"]
    assert revived["second"] == archived["second"]


def test_document_roundtrip_private_owner_scope_and_audit(db_session):
    session = db_session
    project_id, canvas_id = create(session)
    service = CanvasService(session)
    request = edit(service, project_id, canvas_id)
    result = service.commit(project_id, canvas_id, request, "write-one")
    assert result["row_version"] == "2"
    assert service.commit(project_id, canvas_id, request, "write-one") == result
    mine = service.read(project_id, canvas_id, private=True)["source_document"]
    assert mine["nodes"] == request["source_document"]["nodes"]
    assert mine["connections"] == request["source_document"]["connections"]
    assert mine["chatSessions"] == request["source_document"]["chatSessions"]
    public = service.read(project_id, canvas_id)["source_document"]
    assert public["nodes"][0]["metadata"] == {"content": "共同作品"}
    assert "chatSessions" not in public
    add_user(session)
    with Session(session.bind, expire_on_commit=False, autoflush=False) as other:
        other.info["actor"] = actor(2)
        outsider = CanvasService(other)
        with pytest.raises(NotFound):
            outsider.read(project_id, canvas_id, private=True)
        with pytest.raises(NotFound):
            outsider.commit(project_id, canvas_id, request, "outsider-edit")
        with other.begin():
            assert not list(other.scalars(select(CanvasNodeUserState)))
            audit = list(other.scalars(select(AuditEvent)))
            assert not [
                x
                for x in audit
                if x.object_type
                in {"canvas_node_user_states", "canvas_write_receipts", "canvas_user_states"}
            ]
        with pytest.raises(NotFound):
            outsider.read_receipt("write-one")
    mine = service.read(project_id, canvas_id, private=True)["source_document"]
    assert mine["nodes"][0]["metadata"]["prompt"] == "作者私人提示词"
    assert mine["nodes"][0]["metadata"]["content"] == "共同作品"


def test_due_snapshot_reads_metadata_only_and_preserves_twenty_frozen_revisions(
    db_session, monkeypatch
):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    document = edit(service, project_id, canvas_id)["source_document"]
    engine = db_session.get_bind()
    assert (engine.url.database or "").endswith("_test")

    def age_history():
        # 仅为本次随机测试库建立到期前提，不替换生产节流和历史写入。
        with engine.begin() as connection:
            connection.execute(
                update(CanvasRevision.__table__)
                .where(CanvasRevision.canvas_id == canvas_id)
                .values(created_at=utcnow() - timedelta(minutes=6))
            )

    def save(index):
        document["nodes"][0]["metadata"]["content"] = f"历史版本 {index} " + "冻结正文" * 2048
        result = service.commit(
            project_id,
            canvas_id,
            {"expected_row_version": document["revision"], "source_document": document},
            f"snapshot-retention-{index}",
        )
        document["revision"] = result["row_version"]
        return result

    for index in range(21):
        age_history()
        save(index)
    history = service.list_revisions(project_id, canvas_id)["items"]
    assert len(history) == 20
    oldest_id = int(history[-1]["id"])
    with db_session.begin():
        frozen = {
            row.id: deepcopy(row.snapshot_json)
            for row in db_session.scalars(select(CanvasRevision))
        }
    previous = deepcopy(document)
    reading_snapshot = False
    loaded_bodies = []
    original_snapshot = CanvasService._snapshot

    def observe_snapshot(service, canvas, before, *, reason):
        nonlocal reading_snapshot
        reading_snapshot = True
        try:
            return original_snapshot(service, canvas, before, reason=reason)
        finally:
            reading_snapshot = False

    def collect(_connection, _cursor, statement, _parameters, _context, _many):
        if reading_snapshot and re.search(
            r"`?canvas_revisions`?\.`?snapshot_json`?", statement, re.I
        ):
            loaded_bodies.append(reason_for_query)

    reason_for_query = "automatic"
    monkeypatch.setattr(CanvasService, "_snapshot", observe_snapshot)
    event.listen(engine, "before_cursor_execute", collect)
    try:
        age_history()
        result = save(21)
        assert loaded_bodies == [], "节流/回收不应读取最多20份完整历史正文"
        latest_history = service.list_revisions(project_id, canvas_id)["items"]
        assert len(latest_history) == 20
        latest = service.read_revision(project_id, canvas_id, int(latest_history[0]["id"]))
        assert latest["source_document"]["nodes"] == previous["nodes"]
        assert latest["source_document"]["connections"] == previous["connections"]
        with db_session.begin():
            retained = {
                row.id: row.snapshot_json for row in db_session.scalars(select(CanvasRevision))
            }
        assert oldest_id not in retained
        assert all(retained[key] == value for key, value in frozen.items() if key in retained)
        with pytest.raises(NotFound):
            service.read_revision(project_id, canvas_id, oldest_id)
        chosen = int(latest_history[-1]["id"])
        chosen_document = service.read_revision(project_id, canvas_id, chosen)["source_document"]
        reason_for_query = "before_restore"
        restored = service.restore(
            project_id, canvas_id, chosen, int(result["row_version"]), "snapshot-retention-restore"
        )
        assert loaded_bodies == [], "恢复前保留快照同样只应读取历史元数据"
        assert restored["row_version"] == str(int(result["row_version"]) + 1)
        current = service.read(project_id, canvas_id, private=True)["source_document"]
        assert current["nodes"] == chosen_document["nodes"]
        assert current["connections"] == chosen_document["connections"]
        assert len(service.list_revisions(project_id, canvas_id)["items"]) == 20
    finally:
        event.remove(engine, "before_cursor_execute", collect)


def test_cas_keeps_saved_work_and_history_restores_as_new_version(db_session):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    request = edit(service, project_id, canvas_id)
    service.commit(project_id, canvas_id, request, "save-first")
    stale = deepcopy(request)
    stale["source_document"]["nodes"][0]["metadata"]["content"] = "过期窗口内容"
    with pytest.raises(WorkflowError) as error:
        service.commit(project_id, canvas_id, stale, "save-stale")
    assert error.value.code == "canvas_revision_conflict"
    assert error.value.details == {"current_version": "2"}
    assert (
        service.read(project_id, canvas_id)["source_document"]["nodes"][0]["metadata"]["content"]
        == "共同作品"
    )
    history = service.list_revisions(project_id, canvas_id)
    assert len(history["items"]) == 1
    assert history["items"][0]["row_version"] == "1"
    restored = service.restore(project_id, canvas_id, int(history["items"][0]["id"]), 2, "restore")
    assert restored["row_version"] == "3"
    assert service.read(project_id, canvas_id)["source_document"]["nodes"] == []
    history = service.list_revisions(project_id, canvas_id)
    assert history["items"][0]["reason"] == "before_restore"
    assert history["items"][0]["node_count"] == 2


def test_private_viewport_does_not_increment_graph_and_has_own_cas(db_session):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    result = service.update_user_state(
        project_id,
        canvas_id,
        {
            "expected_row_version": "0",
            "viewport": {"x": 101.25, "y": 28.5, "k": 0.3},
            "preferences": {"backgroundMode": "lines"},
        },
    )
    assert result["row_version"] == "1"
    assert service.read(project_id, canvas_id)["row_version"] == "1"
    with pytest.raises(WorkflowError) as error:
        service.update_user_state(
            project_id,
            canvas_id,
            {"expected_row_version": "0", "viewport": {"x": 0, "y": 0, "k": 1}},
        )
    assert error.value.code == "canvas_user_state_conflict"


def test_primary_deletion_selects_remaining_and_last_deletion_is_recoverable(db_session):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    second = service.create_for_project(
        project_id, {"title": "第二个画布", "source_key": "second"}, "create-second"
    )
    archived = service.archive(project_id, canvas_id, 1, "delete-first")
    assert archived["next_canvas_id"] == second["id"]
    assert ProjectService(db_session).get(project_id).primary_canvas_id == int(second["id"])
    last = service.archive(project_id, int(second["id"]), 1, "delete-last")
    assert last["project_archived"]
    assert service.read_receipt("delete-last")["result"] == last
    assert service.archive(project_id, int(second["id"]), 1, "delete-last") == last
    with pytest.raises(NotFound):
        ProjectService(db_session).get(project_id)


def test_decimal_revision_and_guarded_bulk_write(db_session):
    project_id, canvas_id = create(db_session)
    with db_session.begin():
        row = db_session.scalar(select(ProjectCanvas).where(ProjectCanvas.id == canvas_id))
        row.row_version = 9007199254740993
    service = CanvasService(db_session)
    request = edit(service, project_id, canvas_id)
    assert request["expected_row_version"] == "9007199254740993"
    assert (
        service.commit(project_id, canvas_id, request, "precise-version")["row_version"]
        == "9007199254740994"
    )
    with pytest.raises(WorkflowError) as error, db_session.begin():
        db_session.execute(update(CanvasNode).values(x=10))
    assert error.value.code == "guarded_canvas_bulk_write"


def test_simultaneous_same_key_creation_has_one_project(db_session):
    engine = db_session.bind
    key = uuid4().hex

    def invoke():
        with Session(engine, expire_on_commit=False, autoflush=False) as session:
            return create(session, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: invoke(), range(2)))
    assert values[0] == values[1]
    with db_session.begin():
        assert db_session.scalar(select(func.count()).select_from(Project)) == 1
        assert db_session.scalar(select(func.count()).select_from(CanvasRevision)) == 0


def test_owner_reordering_keeps_private_nested_drafts_attached_to_their_source_id(db_session):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    request = edit(service, project_id, canvas_id)
    request["source_document"]["nodes"][0]["metadata"]["storyboard"] = {
        "rows": [
            {"id": "one", "plotDescription": "一", "prompt": "仅作者一"},
            {"id": "two", "plotDescription": "二", "prompt": "仅作者二"},
        ]
    }
    service.commit(project_id, canvas_id, request, "nested-author")
    owner_document = service.read(project_id, canvas_id, private=True)["source_document"]
    rows = owner_document["nodes"][0]["metadata"]["storyboard"]["rows"]
    rows.reverse()
    rows[0]["plotDescription"] = "本人修改"
    rows.insert(0, {"id": "new", "plotDescription": "插入的新项"})
    service.commit(
        project_id,
        canvas_id,
        {
            "expected_row_version": owner_document["revision"],
            "source_document": owner_document,
        },
        "nested-owner",
    )
    db_session.info["actor"] = actor()
    projected = service.read(project_id, canvas_id, private=True)["source_document"]
    actual = projected["nodes"][0]["metadata"]["storyboard"]["rows"]
    assert actual[0] == rows[0]
    assert actual[1] == {"id": "two", "plotDescription": "本人修改", "prompt": "仅作者二"}
    assert actual[2]["prompt"] == "仅作者一"
    shared = service.read(project_id, canvas_id)["source_document"]
    assert all("prompt" not in row for row in shared["nodes"][0]["metadata"]["storyboard"]["rows"])
