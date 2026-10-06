"""Real MySQL verifies atomicity, access scope, CAS and source-document roundtrips."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import Project, ProjectCanvas, ProjectCanvasSettings
from short_drama.domain.canvas import (
    CanvasNode,
    CanvasNodeUserState,
    CanvasRevision,
    CanvasWriteReceipt,
)
from short_drama.domain.collaboration import AuditEvent, ProjectMember, User
from short_drama.schemas.canvas import CanvasCreateRequest
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest
from short_drama.service.base import utcnow
from short_drama.service.canvas_service import CanvasService
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService
from short_drama.service.project_service import ProjectService
from short_drama.utils.snowflake import next_id

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
    add_member(db_session, project_id)
    db_session.info["actor"] = actor(2)
    shared = service.list_workspace(include_documents=True)
    member = next(item for item in shared["items"] if item["id"] == str(canvas_id))
    assert "prompt" not in member["source_document"]["nodes"][0]["metadata"]
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
        {"name": "无限画布", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
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


def add_member(session, project_id, user_id=2):
    with session.begin():
        session.add(
            User(
                id=user_id,
                username=f"member{user_id}",
                display_name="协作者",
                email=f"member{user_id}@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
        session.flush()
        session.add(
            ProjectMember(
                id=next_id(),
                project_id=project_id,
                user_id=user_id,
                status="active",
                joined_at=utcnow(),
            )
        )


def test_create_mode_default_atomic_receipt_and_rollback(db_session, monkeypatch):
    session = db_session
    session.info["actor"] = actor()
    standard = ProjectService(session).create_project({"name": "标准", "aspect": "9:16"})
    assert standard.workspace_mode == "standard" and standard.primary_canvas_id is None
    project_id, canvas_id = create(session)
    assert create(session) == (project_id, canvas_id)
    with session.begin():
        assert session.scalar(select(func.count()).select_from(ProjectCanvas)) == 1
        assert session.scalar(select(func.count()).select_from(CanvasWriteReceipt)) == 1
        assert session.scalar(select(ProjectCanvasSettings.primary_canvas_id)) == canvas_id
    with pytest.raises(WorkflowError) as error:
        ProjectService(session).create_project(
            {"name": "不同内容", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
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
    add_member(session, project_id)
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


def test_document_roundtrip_private_membership_and_audit(db_session):
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
    add_member(session, project_id)
    with Session(session.bind, expire_on_commit=False, autoflush=False) as other:
        other.info["actor"] = actor(2)
        teammate = CanvasService(other)
        their_document = teammate.read(project_id, canvas_id, private=True)["source_document"]
        assert their_document["nodes"][0]["metadata"] == {"content": "共同作品"}
        assert their_document["chatSessions"] == []
        their_document["nodes"][0]["metadata"]["content"] = "成员编辑后的正文"
        teammate.commit(
            project_id,
            canvas_id,
            {"expected_row_version": "2", "source_document": their_document},
            "member-edit",
        )
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
            teammate.read_receipt("write-one")
    mine = service.read(project_id, canvas_id, private=True)["source_document"]
    assert mine["nodes"][0]["metadata"]["prompt"] == "作者私人提示词"
    assert mine["nodes"][0]["metadata"]["content"] == "成员编辑后的正文"
    with session.begin():
        member = session.scalar(select(ProjectMember).where(ProjectMember.user_id == 2))
        member.status = "removed"
        member.removed_at = utcnow()
    with Session(session.bind) as revoked:
        revoked.info["actor"] = actor(2)
        with pytest.raises(NotFound):
            CanvasService(revoked).read(project_id, canvas_id, private=True)


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


def test_member_reordering_keeps_private_nested_drafts_attached_to_their_source_id(db_session):
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
    add_member(db_session, project_id)
    db_session.info["actor"] = actor(2)
    member_document = service.read(project_id, canvas_id, private=True)["source_document"]
    rows = member_document["nodes"][0]["metadata"]["storyboard"]["rows"]
    assert all("prompt" not in row for row in rows)
    rows.reverse()
    rows[0]["plotDescription"] = "协作者修改"
    rows[0]["prompt"] = "仅协作者二"
    rows.insert(0, {"id": "new", "plotDescription": "插入的新项"})
    service.commit(
        project_id,
        canvas_id,
        {
            "expected_row_version": member_document["revision"],
            "source_document": member_document,
        },
        "nested-member",
    )
    db_session.info["actor"] = actor()
    projected = service.read(project_id, canvas_id, private=True)["source_document"]
    actual = projected["nodes"][0]["metadata"]["storyboard"]["rows"]
    assert actual[0] == rows[0]
    assert actual[1] == {"id": "two", "plotDescription": "协作者修改", "prompt": "仅作者二"}
    assert actual[2]["prompt"] == "仅作者一"
    shared = service.read(project_id, canvas_id)["source_document"]
    assert all("prompt" not in row for row in shared["nodes"][0]["metadata"]["storyboard"]["rows"])
    db_session.info["actor"] = actor(2)
    own = service.read(project_id, canvas_id, private=True)["source_document"]
    assert own["nodes"][0]["metadata"]["storyboard"]["rows"][1]["prompt"] == "仅协作者二"
