"""真实 MySQL 事务/权限验证；供应商完成记录由测试替身写入，不发模型请求。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasLibraryAsset,
    CanvasResult,
    CanvasTaskBinding,
    MediaAsset,
    MediaFile,
)
from short_drama.domain.collaboration import AuditEvent, ProjectMember
from short_drama.schemas.canvas_generation import CanvasTaskBindOperation, CanvasTaskRegistration
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.base import utcnow
from short_drama.service.canvas_service import CanvasService
from short_drama.service.canvas_task_outputs import generated_asset_id
from short_drama.service.canvas_task_service import CanvasTaskService
from short_drama.utils.snowflake import next_id
from tests.integration.test_canvas_workspace import actor, add_member, create, node

pytestmark = pytest.mark.integration


def test_bind_waiting_for_project_lock_preserves_the_latest_graph(db_session, monkeypatch):
    from short_drama.dao.canvas_task_dao import CanvasTaskDAO

    project_id, canvas_id, task_id, _, op = prepared(db_session)
    complete(db_session, task_id)
    original = CanvasTaskDAO.canvas
    snapshot_read, moved = Event(), Event()

    def wait_after_snapshot(self, key):
        value = original(self, key)
        snapshot_read.set()
        assert moved.wait(10)
        return value

    monkeypatch.setattr(CanvasTaskDAO, "canvas", wait_after_snapshot)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(CanvasTaskService(db_session).bind, op)
        assert snapshot_read.wait(10)
        try:
            with Session(db_session.get_bind(), expire_on_commit=False, autoflush=False) as other:
                other.info["actor"] = actor()

                def move(doc):
                    doc["nodes"][0].update(
                        title="并发修改的标题", position={"x": 400.25, "y": -30.5}
                    )

                edit_document(other, project_id, canvas_id, move)
        finally:
            moved.set()
        receipt = pending.result()
    assert receipt["result"]["node"]["title"] == "并发修改的标题"
    assert receipt["result"]["node"]["position"] == {"x": 400.25, "y": -30.5}


def test_replay_after_canvas_archive_keeps_historical_receipt_without_reviving_canvas(db_session):
    project_id, canvas_id, task_id, _, op = prepared(db_session)
    complete(db_session, task_id)
    first = CanvasTaskService(db_session).bind(op)
    CanvasService(db_session).archive(
        project_id, canvas_id, int(first["result"]["revision"]), "archive-bound-canvas"
    )
    replay = CanvasTaskService(db_session).bind(op)
    assert replay["replayed"] and replay["result"]["bindingStatus"] == "deleted"
    assert "canvas" not in replay["result"] and "node" not in replay["result"]
    with pytest.raises(NotFound):
        CanvasService(db_session).read(project_id, canvas_id)


def prepared(session, *, kind="text", project=None, key="create-one", user_id=1):
    project_id, canvas_id = project or create(session, key)
    session.info["actor"] = actor(user_id)
    canvases = CanvasService(session)
    document = canvases.read(project_id, canvas_id, private=True)["source_document"]
    document["nodes"] = [node("a")]
    document["nodes"][0]["type"] = kind
    document["nodes"][0]["metadata"] = {"prompt": "本人提示词", "content": ""}
    canvases.commit(
        project_id,
        canvas_id,
        {"expected_row_version": document["revision"], "source_document": document},
        "prepare-" + uuid4().hex,
    )
    config = AIModelConfigService(session).create(
        {
            "service_type": kind,
            "name": "画布契约测试",
            "provider": "openai" if kind != "video" else "ark",
            "model_key": "fixture",
            "base_url": "https://api.openai.com/v1"
            if kind != "video"
            else "https://ark.cn-beijing.volces.com/api/v3",
        }
    )
    payload = {
        "project_id": str(project_id),
        "config_id": str(config.id),
        "input": {"messages": [{"role": "user", "content": "source"}]}
        if kind == "text"
        else {"text": "source"}
        if kind == "audio"
        else {"prompt": "source"},
    }
    if kind == "audio":
        payload["parameters"] = {"voice": "alloy"}
    service = CanvasTaskService(session)
    with session.begin():
        canvases.require_canvas(project_id, canvas_id, lock=True)
        summary, fresh = AIGenerationService(session, SimpleNamespace()).create_locked(
            kind, payload, uuid4().hex
        )
        assert fresh
        task_id = int(summary["generation_id"])
        registration = CanvasTaskRegistration(
            project_id=project_id,
            canvas_id=canvas_id,
            task_id=task_id,
            node_key="a",
            source_node_key="a",
            client_operation_id="operation-" + uuid4().hex,
        )
        binding = service.register_locked(registration)
        assert service.register_locked(registration).id == binding.id
    document = canvases.read(project_id, canvas_id, private=True)["source_document"]
    document["nodes"][0]["metadata"]["taskId"] = str(task_id)
    canvases.commit(
        project_id,
        canvas_id,
        {"expected_row_version": document["revision"], "source_document": document},
        "task-state-" + uuid4().hex,
    )
    operation = CanvasTaskBindOperation.model_validate(
        {
            "opId": f"attach-node:{task_id}:a:0",
            "params": {"canvasId": document["id"], "taskId": str(task_id), "nodeId": "a"},
        }
    )
    return project_id, canvas_id, task_id, binding.id, operation


def complete(session, task_id, *, content="生成的正文"):
    with session.begin():
        task = session.get(AsyncTask, task_id)
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        record.status = "succeeded"
        record.text_content = content if task.service_type == "text" else None
        record.updated_at = record.finished_at = utcnow()
        task.status = "succeeded"
        task.next_action = None
        task.message_status = "idle"
        task.updated_at = task.finished_at = utcnow()
        media_id = None
        if task.service_type != "text":
            media_id = next_id()
            media = MediaFile(
                id=media_id,
                project_id=task.project_id,
                scope_user_id=None,
                created_by=task.initiated_by,
                updated_by=task.initiated_by,
                storage_locator=f"minio://fixture/{media_id}",
                format_code={"image": "image/png", "video": "video/mp4", "audio": "audio/mpeg"}[
                    task.service_type
                ],
                byte_size=100,
                width=64 if task.service_type != "audio" else None,
                height=32 if task.service_type != "audio" else None,
                duration_ms=2000 if task.service_type != "image" else None,
                published_at=None,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(media)
            session.flush()
            session.add(
                MediaAsset(
                    id=next_id(),
                    record_id=record.id,
                    output_index=1,
                    media_id=media.id,
                    media_type=task.service_type,
                    name="生成产物",
                    row_version=1,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
    return media_id


def edit_document(session, project_id, canvas_id, change):
    service = CanvasService(session)
    doc = service.read(project_id, canvas_id, private=True)["source_document"]
    change(doc)
    return service.commit(
        project_id,
        canvas_id,
        {"expected_row_version": doc["revision"], "source_document": doc},
        "edit-" + uuid4().hex,
    )


def test_bind_replay_projects_current_edits_and_never_reapplies_old_content(db_session):
    project_id, canvas_id, task_id, _, op = prepared(db_session)
    complete(db_session, task_id)
    service = CanvasTaskService(db_session)
    first = service.bind(op)
    assert first["result"]["node"]["metadata"]["content"] == "生成的正文"
    assert first["result"]["node"]["position"] == node("a")["position"]
    assert first["result"]["node"]["metadata"]["prompt"] == "本人提示词"

    def change(doc):
        doc["nodes"][0].update(title="新标题", position={"x": 123.875, "y": -75.25})
        doc["nodes"][0]["metadata"]["content"] = "后来手动编辑的作品"

    edit_document(db_session, project_id, canvas_id, change)
    replay = service.bind(op)
    assert replay["replayed"] and replay["result"]["content"] == "后来手动编辑的作品"
    assert replay["result"]["historical"]["content"] == "生成的正文"
    assert replay["result"]["node"]["title"] == "新标题"
    again = service.bind(op.model_copy(update={"op_id": "different-operation"}))
    assert (
        again["result"]["alreadyBound"]
        and again["result"]["revision"] == replay["result"]["revision"]
    )
    with db_session.begin():
        assert db_session.scalar(select(func.count()).select_from(CanvasResult)) == 1


@pytest.mark.parametrize("kind", ["image", "video", "audio"])
def test_media_bind_uses_archived_metadata_and_publishes_only_work(db_session, kind):
    project_id, canvas_id, task_id, _, op = prepared(db_session, kind=kind)
    media_id = complete(db_session, task_id)
    with db_session.begin():
        assert db_session.get(MediaFile, media_id).published_at is None
    receipt = CanvasTaskService(db_session).bind(op)
    result = receipt["result"]
    assert result["storageKey"] == f"resource:{media_id}"
    assert result["node"]["metadata"]["bytes"] == 100
    with db_session.begin():
        assert db_session.get(MediaFile, media_id).published_at is not None
        frozen = db_session.scalar(select(CanvasResult))
        assert frozen.media_id == media_id and frozen.attachment_status == "attached"
        assert (
            db_session.scalar(select(CanvasLibraryAsset)).source_key
            == result["node"]["metadata"]["assetId"]
        )
    add_member(db_session, project_id)
    db_session.info["actor"] = actor(2)
    shared = CanvasService(db_session).read(project_id, canvas_id, private=True)["source_document"]
    metadata = shared["nodes"][0]["metadata"]
    assert metadata["storageKey"] == f"resource:{media_id}"
    assert "taskId" not in metadata and "prompt" not in metadata and "assetId" not in metadata
    with db_session.begin():
        assert db_session.scalar(select(CanvasResult)) is None
        assert db_session.scalar(select(CanvasTaskBinding)) is None
        assert db_session.scalar(select(CanvasLibraryAsset)) is None
    with pytest.raises(NotFound):
        CanvasTaskService(db_session).bind(op)


@pytest.mark.parametrize("kind", ["image", "video", "audio"])
def test_materialize_before_bind_reuses_private_output_and_keeps_asset_edits(db_session, kind):
    project_id, canvas_id, task_id, _, op = prepared(db_session, kind=kind)
    media_id = complete(db_session, task_id)
    service = CanvasTaskService(db_session)
    with db_session.begin():
        canvas = service.canvases.require_canvas(project_id, canvas_id, lock=True)
        task = service.tasks.task(task_id)
        binding = service.tasks.binding(task_id)
        results = service.materialize_locked(canvas, binding, task)
        assert [result.result_index for result in results] == [0]
        assert results[0].attachment_status == "detached"
        asset_key = results[0].content_json["assetId"]
        assert asset_key.startswith("generation_") and len(asset_key) == 75
        asset = db_session.scalar(select(CanvasLibraryAsset))
        assert asset.source_key == asset_key
        assert asset.title == {"image": "生成图片", "video": "生成视频", "audio": "生成音频"}[kind]
        assert asset.payload_json["tags"] == ["生成"]
        assert asset.payload_json["coverUrl"].endswith(f"/{media_id}/file")
        assert asset.payload_json["metadata"]["generationEffectKey"] == f"materialize:{task_id}:0"
        assert asset.payload_json["metadata"]["source"] == "generation-task"
        assert db_session.get(MediaFile, media_id).published_at is None
        asset.title = "本人修改的标题"
        asset.payload_json = {**asset.payload_json, "title": asset.title, "note": "本人备注"}
        frozen_id = results[0].id
    with db_session.begin():
        canvas = service.canvases.require_canvas(project_id, canvas_id, lock=True)
        again = service.materialize_locked(
            canvas, service.tasks.binding(task_id), service.tasks.task(task_id)
        )
        assert [result.id for result in again] == [frozen_id]
    receipt = service.bind(op)
    assert receipt["result"]["assetId"] == asset_key
    with db_session.begin():
        assert db_session.scalar(select(func.count()).select_from(CanvasLibraryAsset)) == 1
        assert db_session.scalar(select(func.count()).select_from(CanvasResult)) == 1
        asset = db_session.scalar(select(CanvasLibraryAsset))
        assert asset.title == "本人修改的标题" and asset.payload_json["note"] == "本人备注"
        assert db_session.get(MediaFile, media_id).published_at is not None


def test_materialize_uses_real_output_slots_without_filling_missing_slots(db_session):
    project_id, canvas_id, task_id, _, _ = prepared(db_session, kind="image")
    media_id = complete(db_session, task_id)
    service = CanvasTaskService(db_session)
    with db_session.begin():
        task = service.tasks.task(task_id)
        record = service.tasks.records(task_id)[-1]
        original = db_session.get(MediaFile, media_id)
        second = MediaFile(
            id=next_id(),
            project_id=original.project_id,
            scope_user_id=None,
            created_by=original.created_by,
            updated_by=original.updated_by,
            storage_locator="minio://fixture/sparse-image-output",
            format_code="image/png",
            byte_size=200,
            width=128,
            height=64,
            published_at=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db_session.add(second)
        db_session.flush()
        db_session.add(
            MediaAsset(
                id=next_id(),
                record_id=record.id,
                output_index=3,
                media_id=second.id,
                media_type="image",
                name="实际第三个输出",
                row_version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        db_session.flush()
        canvas = service.canvases.require_canvas(project_id, canvas_id, lock=True)
        results = service.materialize_locked(canvas, service.tasks.binding(task_id), task)
        assert [result.result_index for result in results] == [0, 2]
        assert [result.media_id for result in results] == [media_id, second.id]
        assert results[1].content_json["naturalWidth"] == 128
        assert results[1].content_json["bytes"] == 200


def test_materialize_unfinished_task_has_no_ready_outputs(db_session):
    project_id, canvas_id, task_id, _, _ = prepared(db_session, kind="image")
    service = CanvasTaskService(db_session)
    with db_session.begin():
        canvas = service.canvases.require_canvas(project_id, canvas_id, lock=True)
        assert (
            service.materialize_locked(
                canvas, service.tasks.binding(task_id), service.tasks.task(task_id)
            )
            == []
        )
        assert db_session.scalar(select(func.count()).select_from(CanvasResult)) == 0
        assert db_session.scalar(select(func.count()).select_from(CanvasLibraryAsset)) == 0


def test_materialize_rejects_existing_asset_with_different_resource_without_overwrite(db_session):
    project_id, canvas_id, task_id, _, _ = prepared(db_session, kind="image")
    media_id = complete(db_session, task_id)
    service = CanvasTaskService(db_session)
    asset_key = generated_asset_id(task_id, 0)
    payload = {"id": asset_key, "data": {"storageKey": "resource:999"}, "note": "保留本人素材"}
    with db_session.begin():
        db_session.add(
            CanvasLibraryAsset(
                **service.canvases.audit(),
                user_id=service.canvases.actor_id,
                project_id=project_id,
                source_key=asset_key,
                kind="image",
                title="既有素材",
                category="material",
                status="confirmed",
                payload_json=payload,
            )
        )
    with pytest.raises(WorkflowError) as error:
        with db_session.begin():
            canvas = service.canvases.require_canvas(project_id, canvas_id, lock=True)
            service.materialize_locked(
                canvas, service.tasks.binding(task_id), service.tasks.task(task_id)
            )
    assert error.value.code == "resource_mismatch" and error.value.status_code == 409
    with db_session.begin():
        assert db_session.scalar(select(func.count()).select_from(CanvasResult)) == 0
        assert db_session.scalar(select(func.count()).select_from(CanvasLibraryAsset)) == 1
        asset = db_session.scalar(select(CanvasLibraryAsset))
        assert asset.title == "既有素材" and asset.payload_json == payload
        assert db_session.get(MediaFile, media_id).published_at is None


@pytest.mark.parametrize("before_bind", [True, False])
@pytest.mark.parametrize("deleted", [True, False])
def test_deleted_or_rebound_node_is_not_resurrected_or_overwritten(
    db_session, before_bind, deleted
):
    project_id, canvas_id, task_id, _, op = prepared(db_session)
    complete(db_session, task_id)
    service = CanvasTaskService(db_session)
    if not before_bind:
        service.bind(op)

    def change(doc):
        if deleted:
            doc["nodes"] = []
        else:
            doc["nodes"][0]["metadata"].update(taskId="999", content="新的生成内容")

    edit_document(db_session, project_id, canvas_id, change)
    if before_bind:
        with pytest.raises(WorkflowError) as error:
            service.bind(op)
        assert error.value.code == ("node_deleted" if deleted else "node_task_mismatch")
    else:
        receipt = service.bind(op)["result"]
        assert receipt["bindingStatus"] == ("deleted" if deleted else "replaced")
    doc = CanvasService(db_session).read(project_id, canvas_id, private=True)["source_document"]
    assert (
        doc["nodes"] == [] if deleted else doc["nodes"][0]["metadata"]["content"] == "新的生成内容"
    )


def test_bind_failure_rolls_back_graph_result_library_and_publication(db_session, monkeypatch):
    project_id, canvas_id, task_id, _, op = prepared(db_session, kind="image")
    media_id = complete(db_session, task_id)
    service = CanvasTaskService(db_session)

    def fail_receipt(**_kwargs):
        raise RuntimeError("injected durable receipt failure")

    monkeypatch.setattr(service.canvases, "record_write", fail_receipt)
    with pytest.raises(RuntimeError, match="durable receipt failure"):
        service.bind(op)
    with db_session.begin():
        assert db_session.get(MediaFile, media_id).published_at is None
        assert db_session.scalar(select(CanvasResult)) is None
        assert db_session.scalar(select(CanvasLibraryAsset)) is None
    assert (
        CanvasService(db_session).read(project_id, canvas_id, private=True)["source_document"][
            "nodes"
        ][0]["metadata"]["content"]
        == ""
    )


def test_registration_and_results_are_private_immutable_and_not_in_shared_audit(db_session):
    project_id, _, task_id, binding_id, op = prepared(db_session)
    complete(db_session, task_id)
    CanvasTaskService(db_session).bind(op)
    with db_session.begin():
        binding = db_session.get(CanvasTaskBinding, binding_id)
        result = db_session.scalar(select(CanvasResult))
        assert not list(
            db_session.scalars(
                select(AuditEvent).where(
                    AuditEvent.object_type.in_(
                        ["canvas_task_bindings", "canvas_results", "canvas_task_media_references"]
                    )
                )
            )
        )
    for row, field, value in [
        (binding, "node_key", "forged"),
        (binding, "source_snapshot", {}),
        (result, "content_json", {"content": "forged"}),
        (result, "attachment_receipt_id", None),
    ]:
        with pytest.raises(WorkflowError):
            with db_session.begin():
                setattr(row, field, value)
                db_session.flush()
    with pytest.raises(WorkflowError):
        with db_session.begin():
            db_session.delete(result)
            db_session.flush()
    with pytest.raises(WorkflowError, match="checked entity writes"):
        with db_session.begin():
            db_session.execute(update(CanvasResult).values(content_json={}))
    add_member(db_session, project_id)
    db_session.info["actor"] = actor(2)
    with pytest.raises(NotFound):
        with db_session.begin():
            binding.context_hash = "f" * 64
            db_session.flush()


def test_revoked_task_author_cannot_bind_or_read_private_history(db_session):
    project_id, canvas_id = create(db_session)
    add_member(db_session, project_id)
    _, _, task_id, _, op = prepared(db_session, project=(project_id, canvas_id), user_id=2)
    complete(db_session, task_id)
    with Session(db_session.get_bind()) as system, system.begin():
        member = system.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == 2
            )
        )
        member.status = "removed"
    with pytest.raises(NotFound):
        CanvasTaskService(db_session).bind(op)
    with db_session.begin():
        assert db_session.scalar(select(CanvasTaskBinding)) is None


@pytest.mark.parametrize("state", ["pending", "empty", "bad_index"])
def test_unready_and_invalid_outputs_do_not_change_graph(db_session, state):
    project_id, canvas_id, task_id, _, op = prepared(db_session)
    if state != "pending":
        complete(db_session, task_id, content="" if state == "empty" else "ready")
    if state == "bad_index":
        op.params.output_index = 1
    before = CanvasService(db_session).read(project_id, canvas_id, private=True)
    with pytest.raises(WorkflowError):
        CanvasTaskService(db_session).bind(op)
    assert CanvasService(db_session).read(project_id, canvas_id, private=True) == before
    with db_session.begin():
        assert db_session.scalar(select(CanvasResult)) is None
