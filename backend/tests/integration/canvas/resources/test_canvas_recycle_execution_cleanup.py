"""真实MySQL中的执行载荷回收；供应商替身不代表真实供应商验收。"""

import base64
import os
import wave
from copy import deepcopy
from datetime import timedelta
from io import BytesIO

import pytest
from sqlalchemy import event, select

from short_drama.ai import GenerationResult
from short_drama.core.exceptions import Conflict
from short_drama.dao.canvas_recycle_cleanup_dao import CanvasRecycleCleanupDAO
from short_drama.dao.canvas_recycle_execution_dao import CanvasRecycleExecutionDAO
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasLocalJob,
    CanvasRenderJob,
    CanvasRenderMediaReference,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskMediaReference,
    CanvasTaskTextDelta,
    CanvasToolRun,
    CanvasToolSubmission,
    MediaAsset,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.canvas import CanvasWriteReceipt
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.utils.snowflake import next_id
from tests.integration.canvas.document.test_canvas_workspace import actor
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    admit,
    bind,
    seed,
)
from tests.integration.canvas.resources.test_canvas_recycle_cleanup import (
    cleanup,
    dispose,
    payload_counts,
    remove_bytes,
)
from tests.integration.canvas.resources.test_canvas_resources import png, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = pytest.mark.integration
GENERATIONS = "/api/v1/ai/generations"


class TextGateway:
    calls = 0

    def validate(self, *_args):
        return {}

    def submit(self, *_args, **_kwargs):
        self.calls += 1
        return GenerationResult("succeeded", "openai_chat.v1", text="已绑定的公开正文")


def frozen_execution(app, task_id):
    with app[1]() as session:
        task = session.get(AsyncTask, int(task_id))
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
        )
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        return {
            "task": (task.id, task.idempotency_key, task.request_hash, task.status),
            "binding": (binding.id, binding.request_hash, binding.context_hash),
            "record": (record.id, record.call_no, record.config_id, record.status),
        }


def keep_project_active(client, project):
    response = client.post(
        f"/api/v1/projects/{project['id']}/canvases",
        headers={"Idempotency-Key": "execution-sibling"},
        json={"title": "保留项目访问的其他画布", "source_key": "execution-sibling"},
    )
    assert response.status_code == 201, response.text


def test_text_payload_is_erased_but_identity_public_binding_and_audit_survive(identity_app):
    client, user, project, path, request = seed(identity_app, "recycle_execution_text")
    keep_project_active(client, project)
    task = admit(client, request)
    gateway = TextGateway()
    executor = GenerationExecutionService(identity_app[1], identity_app[2], gateway, None)
    executor.execute(task["id"], 1)
    bound = bind(client, request, task)
    with identity_app[1].begin() as session:
        record = session.scalar(select(AIGenerationRecord))
        record.credential_cipher = "frozen-fixture-cipher"
        record.response_data = {
            **(record.response_data or {}),
            "private_reply": "私人供应商原文",
            "usage": {"input_tokens": 12, "output_tokens": 9, "private_value": "私人值"},
        }
        binding = session.scalar(select(CanvasTaskBinding))
        delta = "尚未到期的私人正文增量"
        session.add(
            CanvasTaskTextDelta(
                id=next_id(),
                created_at=utcnow(),
                updated_at=utcnow(),
                created_by=int(user["id"]),
                updated_by=int(user["id"]),
                project_id=int(project["id"]),
                canvas_id=int(project["primary_canvas_id"]),
                task_binding_id=binding.id,
                generation_record_id=record.id,
                sequence=1,
                content=delta,
                byte_count=len(delta.encode()),
                expires_at=utcnow() + timedelta(minutes=5),
            )
        )
    frozen = frozen_execution(identity_app, task["id"])
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(identity_app, client, path, document)
    assert cleanup(identity_app)["completed"] == 1
    assert frozen_execution(identity_app, task["id"]) == frozen
    with identity_app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        assert record.credential_cipher is None
        assert record.text_content is None
        assert "input" not in record.request_data and "canvas_request" not in record.request_data
        assert "private_reply" not in record.response_data
        assert record.response_data["usage"] == {"input_tokens": 12, "output_tokens": 9}
        assert session.scalar(select(CanvasTaskBinding)).source_snapshot == {}
        assert session.scalar(select(CanvasTaskTextDelta)) is None
    detail = client.get(f"{GENERATIONS}/{task['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["error"]["code"] == "canvas_work_disposed"
    assert detail.json()["result"]["text"] is None
    assert detail.json()["can_retry"] is detail.json()["can_resume"] is False
    replay = client.post(
        "/api/v1/canvas-runtime/ops/canvas.task.bind",
        json={
            "opId": bound["opId"],
            "params": {
                "canvasId": request["projectId"],
                "nodeId": "runtime-node",
                "taskId": task["id"],
                "outputIndex": 0,
            },
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["result"]["bindingStatus"] == "deleted"
    assert replay.json()["result"]["historical"] == bound["result"]["historical"]
    assert replay.json()["result"]["historical"]["content"] == "已绑定的公开正文"
    executor.execute(task["id"], 1)
    assert gateway.calls == 1
    assert cleanup(identity_app)["examined"] == 0


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_disposal_disables_generic_recovery_and_preserves_original_key(identity_app, status):
    from short_drama.service.task_runtime_service import finish

    client, _, project, path, request = seed(identity_app, "recycle_execution_" + status)
    keep_project_active(client, project)
    task = admit(client, request)
    with identity_app[1].begin() as session:
        finish(session.get(AsyncTask, int(task["id"])), status)
        record = session.scalar(select(AIGenerationRecord))
        record.status = "succeeded"
        record.text_content = "仍可恢复的原模型正文"
        record.response_data = {"finish_reason": "stop"}
        if status == "failed":
            session.get(AsyncTask, int(task["id"])).error = {"code": "business_save_failed"}
    frozen = deepcopy(frozen_execution(identity_app, task["id"]))
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(identity_app, client, path, document)
    assert cleanup(identity_app)["completed"] == 1
    detail = client.get(f"{GENERATIONS}/{task['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["can_retry"] is detail.json()["can_resume"] is False
    assert client.post(f"{GENERATIONS}/{task['id']}/resume").status_code == 409
    retry = client.post(
        f"{GENERATIONS}/{task['id']}/retry", headers={"Idempotency-Key": "disposed-retry"}, json={}
    )
    assert retry.status_code == 409, retry.text
    assert client.post(TASKS, json=request).status_code == 404
    assert frozen_execution(identity_app, task["id"]) == frozen
    with identity_app[1]() as session:
        assert len(list(session.scalars(select(AsyncTask)))) == 1
        assert len(list(session.scalars(select(AIGenerationRecord)))) == 1


@pytest.mark.parametrize("state", ["queued", "unknown", "ended"])
def test_unbound_retry_descendants_defer_then_retire_without_reviving_original_keys(
    identity_app, state
):
    from short_drama.service.task_runtime_service import finish

    client, _, project, path, request = seed(identity_app, "recycle_chain_" + state)
    keep_project_active(client, project)
    root = admit(client, request)
    assert client.post(f"{TASKS}/{root['id']}/cancel").status_code == 200
    identifiers = [root["id"]]
    for index in range(2):
        retry = client.post(
            f"{GENERATIONS}/{identifiers[-1]}/retry",
            headers={"Idempotency-Key": f"original-retry-{index}"},
            json={},
        )
        assert retry.status_code == 202, retry.text
        identifiers.append(retry.json()["generation_id"])
        if index == 0 or state == "ended":
            assert client.post(f"{GENERATIONS}/{identifiers[-1]}/cancel").status_code == 200
    with identity_app[1].begin() as session:
        assert len(list(session.scalars(select(CanvasTaskBinding)))) == 1
        deepest = session.get(AsyncTask, int(identifiers[-1]))
        if state == "unknown":
            finish(deepest, "failed", {"code": "acceptance_unknown"})
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == deepest.id)
            )
            record.status = "unknown"
        frozen = {
            row.id: (row.idempotency_key, row.request_hash)
            for row in session.scalars(select(AsyncTask))
        }
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(identity_app, client, path, document)
    if state != "ended":
        before = payload_counts(identity_app, int(project["primary_canvas_id"]))
        result = cleanup(identity_app)
        assert result["deferred"] == 1 and result["completed"] == 0
        assert payload_counts(identity_app, int(project["primary_canvas_id"])) == before
        with identity_app[1].begin() as session:
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(identifiers[-1]))
            )
            assert "canvas_request" in record.request_data and record.credential_cipher is not None
            if state == "unknown":
                assert record.status == "unknown"
            else:
                assert session.get(AsyncTask, int(identifiers[-1])).status == "queued"
            # 测试显式提供终态证据，不由清理器推测供应商是否已受理。
            finish(session.get(AsyncTask, int(identifiers[-1])), "cancelled")
            record.status = "failed"
    assert cleanup(identity_app)["completed"] == 1
    with identity_app[1]() as session:
        for row in session.scalars(select(AsyncTask)):
            assert (row.idempotency_key, row.request_hash) == frozen[row.id]
            assert row.error["code"] == "canvas_work_disposed"
            assert row.next_action is None and row.message_status == "idle"
        for row in session.scalars(select(AIGenerationRecord)):
            assert row.credential_cipher is None and "input" not in row.request_data
    replay = client.post(
        f"{GENERATIONS}/{root['id']}/retry",
        headers={"Idempotency-Key": "original-retry-0"},
        json={},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["generation_id"] == identifiers[1]
    assert replay.json()["can_retry"] is replay.json()["can_resume"] is False
    changed = client.post(
        f"{GENERATIONS}/{root['id']}/retry",
        headers={"Idempotency-Key": "original-retry-0"},
        json={"config_id": request["logicalModelId"]},
    )
    assert changed.status_code == 409, changed.text
    assert (
        client.post(
            f"{GENERATIONS}/{identifiers[-1]}/retry",
            headers={"Idempotency-Key": "fresh-disposed-key"},
            json={},
        ).status_code
        == 409
    )
    with identity_app[1]() as session:
        assert len(list(session.scalars(select(AsyncTask)))) == 3


def test_v1_payload_marker_is_upgraded_without_losing_task_idempotency(identity_app):
    client, _, project, path, request = seed(identity_app, "recycle_upgrade")
    task = admit(client, request)
    assert client.post(f"{TASKS}/{task['id']}/cancel").status_code == 200
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(identity_app, client, path, document)
    with identity_app[1].begin() as session:
        dao = CanvasRecycleCleanupDAO(session)
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.operation_kind == "canvas.recycle.purge"
            )
        )
        canvas, _ = dao.lock_target(receipt.id, receipt.project_id, receipt.canvas_id)
        dao.remove_payload(canvas.id)
        canvas.properties_json = {"$recycle_cleanup": {"version": 1, "receipt_id": str(receipt.id)}}
        digest = session.get(AsyncTask, int(task["id"])).request_hash
        original_key = session.get(AsyncTask, int(task["id"])).idempotency_key
    assert cleanup(identity_app)["completed"] == 1
    with identity_app[1]() as session:
        canvas = session.get(ProjectCanvas, int(project["primary_canvas_id"]))
        assert canvas.properties_json == {
            "$recycle_cleanup": {"version": 2, "receipt_id": str(receipt.id)}
        }
    # 最后一图归档后整个项目不可读，公开接口仍维持404。
    assert client.get(f"{GENERATIONS}/{task['id']}").status_code == 404
    with identity_app[1]() as session:
        scoped = session.get(AsyncTask, int(task["id"])).idempotency_key
        assert scoped == original_key
        service = AIGenerationService(session, identity_app[2])
        with session.begin_nested():
            replay = service._existing(scoped, digest)
            assert replay[0]["generation_id"] == task["id"] and replay[1] is False
            with pytest.raises(Conflict):
                service._existing(scoped, "0" * 64)


def test_project_owner_disposal_preserves_another_authors_private_execution(identity_app):
    owner, _, project, path, request = seed(identity_app, "recycle_execution_owner")
    keep_project_active(owner, project)
    own_task = admit(owner, request)
    assert owner.post(f"{TASKS}/{own_task['id']}/cancel").status_code == 200
    member, member_user = account(identity_app, "recycle_exec_member")
    member, _, _, _, other_request = seed(identity_app, "unused", client=member, user=member_user)
    other_request["input"]["config"]["systemPrompt"] = "其他作者的私人系统指令"
    other_task = admit(member, other_request)
    assert member.post(f"{TASKS}/{other_task['id']}/cancel").status_code == 200
    other_before = member.get(f"{GENERATIONS}/{other_task['id']}").json()
    with identity_app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(other_task["id"]))
        )
        frozen = (
            deepcopy(record.request_data),
            record.credential_cipher,
            deepcopy(record.config_snapshot),
        )
    document = owner.get(path + "/my-document").json()["source_document"]
    dispose(identity_app, owner, path, document)
    assert cleanup(identity_app)["completed"] == 1
    assert member.get(f"{GENERATIONS}/{other_task['id']}").json() == other_before
    assert owner.get(f"{GENERATIONS}/{other_task['id']}").status_code == 404
    with identity_app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(other_task["id"]))
        )
        assert (record.request_data, record.credential_cipher, record.config_snapshot) == frozen
        assert session.get(AsyncTask, int(own_task["id"])).error["code"] == "canvas_work_disposed"


def test_execution_redaction_rolls_back_with_failed_graph_cleanup_commit(identity_app):
    client, _, project, path, request = seed(identity_app, "recycle_execution_rollback")
    task = admit(client, request)
    assert client.post(f"{TASKS}/{task['id']}/cancel").status_code == 200
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(identity_app, client, path, document)
    with identity_app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        frozen = (
            deepcopy(record.request_data),
            record.credential_cipher,
            deepcopy(record.config_snapshot),
        )
        version = session.get(AsyncTask, int(task["id"])).message_version
    before = payload_counts(identity_app, int(project["primary_canvas_id"]))

    def fail_commit(session):
        assert session.scalar(select(AIGenerationRecord)).credential_cipher is None
        assert session.get(AsyncTask, int(task["id"])).error["code"] == "canvas_work_disposed"
        raise RuntimeError("测试：擦除执行载荷后提交失败")

    event.listen(identity_app[1].class_, "before_commit", fail_commit)
    try:
        result = cleanup(identity_app)
    finally:
        event.remove(identity_app[1].class_, "before_commit", fail_commit)
    assert result["failed"] == 1 and result["completed"] == 0
    assert payload_counts(identity_app, int(project["primary_canvas_id"])) == before
    with identity_app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        assert (record.request_data, record.credential_cipher, record.config_snapshot) == frozen
        assert session.get(AsyncTask, int(task["id"])).message_version == version
    assert cleanup(identity_app)["completed"] == 1


def test_actor_session_cannot_invoke_execution_payload_maintenance(identity_app):
    _, user = account(identity_app, "recycle_execution_actor")
    with identity_app[1](info={"actor": actor(int(user["id"]))}) as session:
        with pytest.raises(ValueError, match="internal maintenance"):
            CanvasRecycleExecutionDAO(session)


@pytest.mark.skipif(os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1", reason="需隔离MinIO")
def test_released_task_input_is_reclaimed_while_independent_generated_library_is_preserved(
    resource_app,
):
    client, _, project, path, request = seed(resource_app, "recycle_media_audit", kind="image")
    keep_project_active(client, project)
    source = upload(client, png("red"), key="recycle-generation-input", source=request["projectId"])
    assert source.status_code == 200, source.text
    resource = source.json()["resource"]
    request["input"]["referenceImages"] = [
        {"id": "source", "storageKey": "resource:" + resource["id"], "type": "image/png"}
    ]
    task = admit(client, request)
    content = png("purple")

    class ImageGateway:
        calls = 0

        def validate(self, *_args):
            return {}

        def submit(self, *_args, **_kwargs):
            self.calls += 1
            return GenerationResult(
                "succeeded",
                "openai_images.v1",
                outputs=[{"base64": base64.b64encode(content).decode()}],
            )

    gateway = ImageGateway()
    executor = GenerationExecutionService(
        resource_app[1], resource_app[2], gateway, resource_app[0].state.storage
    )
    executor.execute(task["id"], 1)
    with resource_app[1]() as session:
        version = session.get(AsyncTask, int(task["id"])).message_version
        assert session.scalar(select(CanvasTaskMediaReference)) is not None
    executor.execute(task["id"], version)
    ready = client.get(f"{TASKS}/{task['id']}")
    assert ready.status_code == 200, ready.text
    library_id = ready.json()["outputs"][0]["materializedAssetId"]
    asset_path = "/api/v1/canvas-runtime/assets/" + library_id
    library = client.get(asset_path).json()
    with resource_app[1]() as session:
        output = session.scalar(select(MediaAsset))
        output_id = output.media_id
        record = session.scalar(select(AIGenerationRecord))
        manifest = deepcopy(record.response_data["media_manifest"])
        assert session.get(MediaFile, output_id).storage_locator.startswith("minio://canvas-test-")
    bind(client, request, task)
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(resource_app, client, path, document)
    result = cleanup(resource_app)
    assert result["completed"] == result["retired_uploads"] == 1 and result["failed"] == 0
    assert client.get(asset_path).json() == library
    output_url = f"/api/v1/canvas-runtime/resources/{output_id}/file"
    assert client.get(output_url).content == content
    with resource_app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        assert record.credential_cipher is None and "canvas_request" not in record.request_data
        retained = record.response_data["media_manifest"][0]
        for key in ("asset_id", "locator", "checksum", "output_index", "saved"):
            assert retained[key] == manifest[0][key]
        assert "source_cipher" not in retained and "inline_cipher" not in retained
        assert session.scalar(select(CanvasTaskMediaReference)) is None
        assert session.get(MediaFile, int(resource["id"])) is None
        assert session.scalar(select(CanvasResult)).content_json == {}
        assert session.scalar(select(MediaAsset)).media_id == output_id
    assert remove_bytes(resource_app)["completed"] == 1
    assert client.get(output_url).content == content
    executor.execute(task["id"], version)
    assert gateway.calls == 1


@pytest.mark.skipif(os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1", reason="需隔离MinIO")
def test_idle_tool_and_terminal_local_payloads_release_their_media_pins(
    resource_app,
):
    client, user, project, path, request = seed(resource_app, "recycle_local_payload")
    keep_project_active(client, project)
    image = upload(client, png(), key="recycle-tool-image", source=request["projectId"])
    assert image.status_code == 200, image.text
    content = BytesIO()
    with wave.open(content, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\0\0" * 2400)
    audio = upload(
        client,
        content.getvalue(),
        key="recycle-local-audio",
        source=request["projectId"],
        kind="audio",
        name="input.wav",
        mime="audio/wav",
    )
    assert audio.status_code == 200, audio.text
    now = utcnow()

    def child():
        return {
            "id": next_id(),
            "created_at": now,
            "updated_at": now,
            "created_by": int(user["id"]),
            "updated_by": int(user["id"]),
            "user_id": int(user["id"]),
            "project_id": int(project["id"]),
            "canvas_id": int(project["primary_canvas_id"]),
        }

    # 这里只写入合法的历史/待批准ORM状态；不执行审美、ASR或渲染Worker。
    with resource_app[1].begin() as session:
        run = CanvasToolRun(
            **child(),
            client_key="recycle-tool-idle",
            create_hash="1" * 64,
            state_json={"kind": "art-critique", "analysis": "私有分析正文"},
            source_snapshot={"prompt": "私有来源快照"},
            source_hash="2" * 64,
            source_media_id=int(image.json()["resource"]["id"]),
            model_config_id=int(request["logicalModelId"]),
            config_hash="3" * 64,
        )
        session.add(run)
        session.flush()
        item = CanvasToolSubmission(
            **child(),
            run_id=run.id,
            item_key="stage:review_art_composition",
            request_hash="4" * 64,
            request_json=request,
            execution_json={"prompt": "私人阶段请求"},
        )
        session.add(item)
        job = CanvasLocalJob(
            **child(),
            client_operation_id="recycle-local-task",
            request_hash="5" * 64,
            task_type="timeline_transcription",
            request_json={"input": "私有转写参数"},
            source_snapshot={"source": "私有音频快照"},
            source_media_id=int(audio.json()["resource"]["id"]),
            canvas_version=1,
            status="succeeded",
            finished_at=now,
            next_run_at=now,
            result_json={"text": "私有字幕正文"},
        )
        session.add(job)
        render = CanvasRenderJob(
            **child(),
            client_operation_id="recycle-render-task",
            request_hash="6" * 64,
            request_json={"title": "私人渲染指令"},
            plan_json={"clips": ["私有片段"]},
            source_snapshot={"timeline": "私有时间线"},
            canvas_version=1,
            status="failed",
            finished_at=now,
            next_run_at=now,
            error_json={"code": "render_failed", "message": "私有诊断"},
        )
        session.add(render)
        session.flush()
        session.add(
            CanvasRenderMediaReference(
                **child(),
                job_id=render.id,
                source_key="source-audio",
                ordinal=0,
                media_id=int(audio.json()["resource"]["id"]),
            )
        )
        identities = (run.id, item.id, job.id, render.id)
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(resource_app, client, path, document)
    result = cleanup(resource_app)
    assert result["completed"] == 1
    assert result["retired_uploads"] == 2 and result["retained_uploads"] == 0
    with resource_app[1]() as session:
        run = session.get(CanvasToolRun, identities[0])
        item = session.get(CanvasToolSubmission, identities[1])
        assert run.state_json == run.source_snapshot == {} and run.source_media_id is None
        assert run.create_hash == "1" * 64 and run.source_hash == "2" * 64
        assert item.request_json == item.execution_json == {} and item.request_hash == "4" * 64
        job = session.get(CanvasLocalJob, identities[2])
        assert job.status == "succeeded" and job.result_json == {}
        assert job.source_media_id is None and job.disposal_receipt_id is not None
        assert job.request_json == job.source_snapshot == {}
        render = session.get(CanvasRenderJob, identities[3])
        assert render.status == "failed" and render.request_json == render.plan_json == {}
        assert render.error_json["code"] == "canvas_work_disposed"
        assert render.disposal_receipt_id == job.disposal_receipt_id
        assert session.scalar(select(CanvasRenderMediaReference)) is None
        assert session.get(MediaFile, int(audio.json()["resource"]["id"])) is None
        assert session.get(MediaFile, int(image.json()["resource"]["id"])) is None
    assert remove_bytes(resource_app)["completed"] == 2
    audio_id = audio.json()["resource"]["id"]
    assert client.get(f"/api/v1/canvas-runtime/resources/{audio_id}/file").status_code == 404


@pytest.mark.parametrize("reference", ["batch", "agent"])
def test_external_audit_foreign_keys_preserve_terminal_execution_payload(identity_app, reference):
    from short_drama.domain import (
        AgentConversation,
        AgentMessage,
        AgentRun,
        AgentToolCall,
        AgentTurn,
        GenerationBatchItem,
        GenerationBatchJob,
    )

    client, user, project, path, request = seed(
        identity_app, "recycle_external_" + reference, kind="image"
    )
    task = admit(client, request)
    assert client.post(f"{TASKS}/{task['id']}/cancel").status_code == 200
    # 用真实外键构造仍被其他审计链引用的终态；不是批次/Agent运行验收。
    with identity_app[1].begin() as session:
        now = utcnow()
        if reference == "batch":
            batch = GenerationBatchJob(
                id=next_id(),
                project_id=int(project["id"]),
                scope_user_id=None,
                initiated_by=int(user["id"]),
                scene="asset_image",
                config_id=int(request["logicalModelId"]),
                config_version=1,
                scope={"project_id": project["id"]},
                status="cancelled",
                idempotency_key="protected-batch",
                request_hash="1" * 64,
                created_at=now,
                updated_at=now,
            )
            session.add(batch)
            session.flush()
            session.add(
                GenerationBatchItem(
                    id=next_id(),
                    batch_id=batch.id,
                    source_id=next_id(),
                    name="保留的审计项",
                    task_id=int(task["id"]),
                    status="cancelled",
                )
            )
        else:
            conversation = AgentConversation(
                id=next_id(),
                owner_user_id=int(user["id"]),
                project_id=int(project["id"]),
                title="私人审计对话",
            )
            message = AgentMessage(
                id=next_id(),
                conversation_id=conversation.id,
                seq=1,
                role="user",
                content="保留审计",
            )
            run = AgentRun(
                id=next_id(),
                conversation_id=conversation.id,
                trigger_message_id=message.id,
                initiated_by=int(user["id"]),
                model_config_id=int(request["logicalModelId"]),
                status="cancelled",
                finished_at=now,
                created_at=now,
                updated_at=now,
            )
            turn = AgentTurn(id=next_id(), run_id=run.id, turn_no=1)
            tool = AgentToolCall(
                id=next_id(),
                run_id=run.id,
                turn_id=turn.id,
                call_index=1,
                provider_call_id="protected-call",
                tool_name="generate_image",
                arguments_hash="2" * 64,
                idempotency_key="3" * 64,
                generation_task_id=int(task["id"]),
                status="cancelled",
            )
            for row in (conversation, message, run, turn, tool):
                session.add(row)
                session.flush()
        record = session.scalar(select(AIGenerationRecord))
        frozen = (
            deepcopy(record.request_data),
            record.credential_cipher,
            deepcopy(record.config_snapshot),
        )
        source = deepcopy(session.scalar(select(CanvasTaskBinding)).source_snapshot)
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(identity_app, client, path, document)
    assert cleanup(identity_app)["completed"] == 1
    with identity_app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        assert (record.request_data, record.credential_cipher, record.config_snapshot) == frozen
        assert session.scalar(select(CanvasTaskBinding)).source_snapshot == source
        assert session.get(AsyncTask, int(task["id"])).error is None


def test_concurrent_maintenance_redacts_a_terminal_task_only_once(identity_app, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, _, _, path, request = seed(identity_app, "recycle_execution_race")
    task = admit(client, request)
    assert client.post(f"{TASKS}/{task['id']}/cancel").status_code == 200
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(identity_app, client, path, document)
    with identity_app[1]() as session:
        version = session.get(AsyncTask, int(task["id"])).message_version
    barrier, original = Barrier(2), CanvasRecycleCleanupDAO.candidates

    def synchronize(dao, after_id):
        result = original(dao, after_id)
        if after_id == 0:
            barrier.wait(timeout=10)
        return result

    with monkeypatch.context() as fault:
        fault.setattr(CanvasRecycleCleanupDAO, "candidates", synchronize)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: cleanup(identity_app), range(2)))
    assert sum(result["completed"] for result in results) == 1
    assert sum(result["failed"] for result in results) == 0
    with identity_app[1]() as session:
        assert session.scalar(select(AIGenerationRecord)).credential_cipher is None
        assert session.get(AsyncTask, int(task["id"])).message_version == version + 1
