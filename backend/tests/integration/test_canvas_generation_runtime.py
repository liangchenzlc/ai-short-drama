"""源任务 HTTP 准入与真实 MySQL/Worker；供应商受控替身，不请求付费模型。"""

import base64
import hashlib
import json
import os
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from short_drama.ai import GenerationError, GenerationResult
from short_drama.core.exceptions import StorageUnavailable
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    MediaAsset,
    MediaFile,
)
from short_drama.service.canvas_task_service import CanvasTaskService
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_resources import png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_workspace import actor
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
TASKS = "/api/v1/canvas-runtime/tasks"
WORKSPACE = "/api/v1/canvas-runtime/workspace/model-config"


def seed(identity_app, username, *, client=None, user=None, project=None, kind="text"):
    if client is None:
        client, user = account(identity_app, username)
    client.headers["X-Canvas-Actor"] = user["id"]
    if project is None:
        response = client.post(
            "/api/v1/projects",
            headers={"Idempotency-Key": "project-" + uuid4().hex},
            json={
                "name": "画布生成垂直验证",
                "aspect": "16:9",
                "workspace_mode": "infinite_canvas",
            },
        )
        assert response.status_code == 201, response.text
        project = response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = client.get(path + "/my-document").json()
    document = initial["source_document"]
    document["nodes"] = [
        {
            "id": "runtime-node",
            "type": kind,
            "title": "已保存的来源",
            "position": {"x": 420.25, "y": -20.5},
            "width": 320,
            "height": 220,
            "metadata": {"content": "原来的作品", "prompt": "生成源提示词"},
        }
    ]
    saved = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "seed-" + uuid4().hex},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    model_keys = {
        "text": "gpt-4o-mini",
        "image": "gpt-image-1",
        "video": "doubao-seedance-1-0-pro-250528",
        "audio": "gpt-4o-mini-tts",
    }
    protocols = {
        "text": "chat-completion",
        "image": "openai-image",
        "video": "volcengine-ark-video",
        "audio": "openai-audio",
    }
    model = client.post(
        "/api/v1/ai-model-configs",
        json={
            "service_type": kind,
            "name": "画布测试模型",
            "provider": "ark" if kind == "video" else "openai",
            "model_key": model_keys[kind],
            "base_url": "https://ark.cn-beijing.volces.com/api/v3"
            if kind == "video"
            else "https://api.openai.com/v1",
            "apikey": "canvas-generation-runtime-placeholder-key",
            "headers": [
                {
                    "name": "X-Generation-Runtime-Fixture",
                    "value": "canvas-runtime-placeholder-header",
                }
            ],
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": protocols[kind]},
        },
    )
    assert model.status_code == 201, model.text
    saved_model = model.json()
    current = client.get(WORKSPACE)
    assert current.status_code == 200, current.text
    workspace = current.json()
    selection = f"host-{saved_model['id']}::{saved_model['model_key']}"
    selected = client.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "model": selection,
                f"{kind}Model": selection,
            },
        },
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["channels"] == []
    assert any(item["id"] == saved_model["id"] for item in selected.json()["models"])
    request = {
        "projectId": document["id"],
        "type": "canvas_" + kind,
        "operation": "text_to_video" if kind == "video" else kind,
        "prompt": "生成源提示词",
        "model": saved_model["model_key"],
        "logicalModelId": saved_model["id"],
        "input": {
            "mode": kind,
            "prompt": "生成源提示词",
            "config": {"systemPrompt": "系统上下文"},
            "textHistory": [{"role": "user", "content": "上一条输入"}],
            "textOptions": {"stream": False, "thinking": False},
            "metadata": {
                "nodeId": "runtime-node",
                "sourceNodeId": "runtime-node",
                "clientOperationId": "runtime-" + uuid4().hex,
            },
        },
    }
    if kind != "text":
        request["input"]["config"] = {
            "image": {"count": "1", "size": "1:1"},
            "video": {"size": "16:9", "videoSeconds": "5", "vquality": "720"},
            "audio": {"audioVoice": "alloy", "audioFormat": "mp3", "audioSpeed": "1"},
        }[kind]
        request["input"]["textHistory"] = []
    return client, user, project, path, request


def admit(client, request):
    response = client.post(TASKS, json=request)
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["id"].isdecimal() and isinstance(task["id"], str)
    assert task["projectId"] == request["projectId"] and task["status"] == "queued"
    assert "data" not in task and "success" not in task
    return task


def records(identity_app, user):
    with identity_app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        return {
            "tasks": session.scalar(select(func.count()).select_from(AsyncTask)),
            "records": session.scalar(select(func.count()).select_from(AIGenerationRecord)),
            "bindings": session.scalar(select(func.count()).select_from(CanvasTaskBinding)),
        }


def test_admission_freezes_saved_source_and_replays_without_advancing_shared_revision(identity_app):
    client, user, _, path, request = seed(identity_app, "canvas_generation_admission")
    before = client.get(path + "/my-document").json()
    task = admit(client, request)
    after = client.get(path + "/my-document").json()
    assert after["row_version"] == before["row_version"]
    assert after["source_document"]["nodes"][0]["metadata"]["taskId"] == task["id"]
    assert records(identity_app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    with identity_app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        binding = session.scalar(select(CanvasTaskBinding))
        assert binding.async_task_id == int(task["id"])
        assert binding.node_key == binding.source_node_key == "runtime-node"
        frozen = deepcopy(binding.source_snapshot)
        assert "生成源提示词" in json.dumps(frozen, ensure_ascii=False)
    repeat = client.post(TASKS, json=request)
    assert repeat.status_code == 200 and repeat.json()["id"] == task["id"]
    changed = deepcopy(request)
    changed["prompt"] = changed["input"]["prompt"] = "另一个提示词"
    assert client.post(TASKS, json=changed).status_code == 409
    assert records(identity_app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    document = after["source_document"]
    document["nodes"][0]["metadata"]["prompt"] = "后来已保存的输入"
    saved = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "edit-" + uuid4().hex},
        json={"expected_row_version": after["row_version"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    with identity_app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        assert session.scalar(select(CanvasTaskBinding)).source_snapshot == frozen
    listed = client.get(TASKS, params={"projectId": request["projectId"], "pageSize": 30}).json()
    assert isinstance(listed, list) and [item["id"] for item in listed] == [task["id"]]


def test_admission_failure_rolls_back_task_record_binding_and_private_target(
    identity_app, monkeypatch
):
    client, user, _, path, request = seed(identity_app, "canvas_generation_atomic")
    before = client.get(path + "/my-document").json()

    def fail_registration(*_args, **_kwargs):
        raise RuntimeError("controlled admission registration failure")

    monkeypatch.setattr(CanvasTaskService, "register_locked", fail_registration)
    with pytest.raises(RuntimeError, match="admission registration failure"):
        client.post(TASKS, json=request)
    assert records(identity_app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert client.get(path + "/my-document").json() == before


@pytest.mark.parametrize("field", ["nodeId", "sourceNodeId"])
def test_missing_saved_source_or_target_never_admits_a_task(identity_app, field):
    client, user, _, _, request = seed(identity_app, "canvas_generation_missing")
    request["input"]["metadata"][field] = "not-saved"
    assert client.post(TASKS, json=request).status_code in {404, 409}
    assert records(identity_app, user) == {"tasks": 0, "records": 0, "bindings": 0}


def test_invalid_canvas_image_size_never_admits_a_task(identity_app):
    client, user, _, path, request = seed(
        identity_app, "canvas_generation_size_boundary", kind="image"
    )
    before = client.get(path + "/my-document").json()
    request["input"]["config"]["size"] = "not-a-ratio"
    response = client.post(TASKS, json=request)
    assert response.status_code == 422, response.text
    assert records(identity_app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert client.get(path + "/my-document").json() == before


def test_task_and_model_are_author_private_even_for_project_members(identity_app):
    owner, user, project, _, request = seed(identity_app, "canvas_generation_owner")
    task = admit(owner, request)
    member, other = account(identity_app, "canvas_generation_reader")
    member.headers["X-Canvas-Actor"] = other["id"]
    join(identity_app, owner, member, project["id"], other["id"])
    assert member.get(TASKS).json() == []
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert owner.get(f"{TASKS}/{task['id']}/logs").status_code == 200
    assert isinstance(owner.get(f"{TASKS}/{task['id']}/logs").json(), list)
    assert member.get(f"{TASKS}/{task['id']}/logs").status_code == 404
    assert member.post(f"{TASKS}/{task['id']}/cancel").status_code == 404
    another = deepcopy(request)
    another["input"]["metadata"]["clientOperationId"] = "member-" + uuid4().hex
    assert member.post(TASKS, json=another).status_code == 404
    assert records(identity_app, user) == {"tasks": 1, "records": 1, "bindings": 1}


def bind(client, request, task):
    payload = {
        "opId": "runtime-bind-" + uuid4().hex,
        "params": {
            "canvasId": request["projectId"],
            "taskId": task["id"],
            "nodeId": request["input"]["metadata"]["nodeId"],
            "outputIndex": 0,
        },
    }
    response = client.post("/api/v1/canvas-runtime/ops/canvas.task.bind", json=payload)
    assert response.status_code == 200, response.text
    replay = client.post("/api/v1/canvas-runtime/ops/canvas.task.bind", json=payload)
    assert replay.status_code == 200 and replay.json()["replayed"]
    return response.json()


def test_text_worker_delivers_a_private_draft_before_the_source_bind(identity_app):
    client, user, project, path, request = seed(identity_app, "canvas_generation_text_worker")
    member, other = account(identity_app, "canvas_generation_text_reader")
    join(identity_app, client, member, project["id"], other["id"])
    member.headers["X-Canvas-Actor"] = other["id"]
    task = admit(client, request)
    before = client.get(path + "/my-document").json()

    class Gateway:
        calls = 0

        def validate(self, _snapshot, frozen, _adapter):
            assert frozen["input"]["messages"] == [
                {"role": "system", "content": "系统上下文"},
                {"role": "user", "content": "上一条输入"},
                {"role": "user", "content": "生成源提示词"},
            ]
            return {}

        def submit(self, *_args, **_kwargs):
            self.calls += 1
            return GenerationResult("succeeded", "openai_chat.v1", text="执行器真实保存的正文")

    gateway = Gateway()
    executor = GenerationExecutionService(identity_app[1], identity_app[2], gateway, None)
    executor.execute(task["id"], 1)
    executor.execute(task["id"], 1)
    result = client.get(f"{TASKS}/{task['id']}")
    assert result.status_code == 200, result.text
    current = result.json()
    assert current["status"] == "succeeded" and current["resultState"] == "READY"
    assert current["textDraft"] == "执行器真实保存的正文"
    assert json.loads(current["resultJson"]) == {"mode": "text", "text": current["textDraft"]}
    assert client.get(path + "/my-document").json() == before
    assert "执行器真实保存的正文" not in json.dumps(member.get(path).json(), ensure_ascii=False)
    with identity_app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        delivered = session.scalar(select(CanvasResult))
        assert delivered.attachment_status == "detached"
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 0
    bound = bind(client, request, task)
    assert bound["result"]["node"]["metadata"]["content"] == current["textDraft"]
    assert int(bound["result"]["revision"]) == int(before["row_version"]) + 1
    assert (
        member.get(path).json()["source_document"]["nodes"][0]["metadata"]["content"]
        == (current["textDraft"])
    )
    assert gateway.calls == 1


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
    reason="Enable actual isolated canvas generation MinIO verification",
)
@pytest.mark.parametrize("lost_put_ack", [False, True])
def test_image_worker_archives_bytes_and_materializes_before_shared_bind(
    resource_app, monkeypatch, lost_put_ack
):
    client, user, project, path, request = seed(
        resource_app, "canvas_generation_image_worker", kind="image"
    )
    member, other = account(resource_app, "canvas_generation_image_reader")
    join(resource_app, client, member, project["id"], other["id"])
    member.headers["X-Canvas-Actor"] = other["id"]
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    content = png("purple")
    storage = resource_app[0].state.storage
    real_put = storage.put
    puts = 0

    def put_with_controlled_ack_loss(*args, **kwargs):
        nonlocal puts
        puts += 1
        result = real_put(*args, **kwargs)
        if lost_put_ack and puts == 1:
            raise StorageUnavailable("Controlled lost PUT acknowledgement after real object write")
        return result

    monkeypatch.setattr(storage, "put", put_with_controlled_ack_loss)

    class Gateway:
        calls = 0

        def validate(self, _snapshot, frozen, _adapter):
            assert frozen["parameters"] == {"count": 1}
            assert frozen["canvas_parameters"] == {
                "mode": "image",
                "size": "1:1",
                "quality": "auto",
                "transparent_background": False,
            }
            return {}

        def submit(self, *_args, **_kwargs):
            self.calls += 1
            return GenerationResult(
                "succeeded",
                "openai_images.v1",
                outputs=[{"base64": base64.b64encode(content).decode()}],
            )

    gateway = Gateway()
    executor = GenerationExecutionService(resource_app[1], resource_app[2], gateway, storage)
    executor.execute(task["id"], 1)
    pending = client.get(f"{TASKS}/{task['id']}")
    assert pending.status_code == 200, pending.text
    assert pending.json()["status"] == "running"
    assert pending.json()["stage"] == "saving"
    assert pending.json()["resultState"] == "PENDING_MATERIALIZATION"
    assert pending.json()["outputs"] == []
    with resource_app[1]() as session:
        current = session.get(AsyncTask, int(task["id"]))
        version = current.message_version
        assert current.next_action == "save" and version > 1
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 0
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0
    assert client.get(path + "/my-document").json() == before
    executor.execute(task["id"], version)
    ready = client.get(f"{TASKS}/{task['id']}")
    assert ready.status_code == 200, ready.text
    completed = ready.json()
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    media = json.loads(completed["resultJson"])["images"][0]
    assert (media["width"], media["height"], media["bytes"]) == (37, 19, len(content))
    assert media["mimeType"] == "image/png" and media["storageKey"].startswith("resource:")
    assert client.get(media["url"]).content == content
    assert member.get(media["url"]).status_code == 404
    assert client.get(path + "/my-document").json() == before
    library_id = completed["outputs"][0]["materializedAssetId"]
    asset_path = "/api/v1/canvas-runtime/assets/" + library_id
    asset = client.get(asset_path).json()["asset"]
    assert asset["data"]["storageKey"] == media["storageKey"]
    assert member.get(asset_path).status_code == 404
    asset["title"] = "用户调整后的生成素材名称"
    edited = client.put(asset_path, json={"asset": asset})
    assert edited.status_code == 200, edited.text
    assert client.get(f"{TASKS}/{task['id']}").json() == completed
    assert client.get(asset_path).json()["asset"]["title"] == asset["title"]
    with resource_app[1]() as session:
        session.info["actor"] = actor(int(user["id"]))
        archived = session.scalar(select(MediaFile))
        assert archived.checksum_sha256 == hashlib.sha256(content).hexdigest()
        assert archived.storage_locator.startswith("minio://canvas-test-")
        result = session.scalar(select(CanvasResult))
        assert result.attachment_status == "detached" and result.media_id == archived.id
    bound = bind(client, request, task)
    node = bound["result"]["node"]
    assert node["metadata"]["assetId"] == library_id
    assert node["metadata"]["storageKey"] == media["storageKey"]
    assert member.get(media["url"]).status_code == 200
    assert member.get(media["url"]).content == content
    shared = member.get(path).json()["source_document"]["nodes"][0]
    assert shared["metadata"]["storageKey"] == media["storageKey"]
    assert "assetId" not in shared["metadata"] and "taskId" not in shared["metadata"]
    assert member.get(asset_path).status_code == 404
    assert client.get(asset_path).json()["asset"]["title"] == asset["title"]
    executor.execute(task["id"], 1)
    executor.execute(task["id"], version)
    assert gateway.calls == 1 and puts == 1


def test_revoked_author_cannot_read_cancel_resume_or_replay_a_task(identity_app):
    owner, owner_user, project, _, _ = seed(identity_app, "canvas_generation_project_owner")
    member, user = account(identity_app, "canvas_generation_author")
    join(identity_app, owner, member, project["id"], user["id"])
    member, _, _, _, request = seed(
        identity_app, "unused", client=member, user=user, project=project
    )
    task = admit(member, request)
    assert owner.delete(f"/api/v1/projects/{project['id']}/members/{user['id']}").status_code == 200
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert member.post(f"{TASKS}/{task['id']}/cancel").status_code == 404
    assert member.post(f"{TASKS}/{task['id']}/resume").status_code == 404
    assert member.post(TASKS, json=request).status_code == 404
    assert owner.get(TASKS).json() == []
    assert records(identity_app, owner_user)["tasks"] == 0


def test_queued_cancel_prevents_the_worker_from_submitting(identity_app):
    client, _, _, _, request = seed(identity_app, "canvas_generation_cancel")
    task = admit(client, request)
    cancelled = client.post(f"{TASKS}/{task['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    class Gateway:
        calls = 0

        def validate(self, *_args):
            return {}

        def submit(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("cancelled generation must not submit")

    gateway = Gateway()
    executor = GenerationExecutionService(identity_app[1], identity_app[2], gateway, None)
    executor.execute(task["id"], 1)
    assert gateway.calls == 0
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "cancelled"


def test_unknown_acceptance_is_not_resubmitted_or_admitted_as_a_new_task(identity_app):
    client, user, _, _, request = seed(identity_app, "canvas_generation_unknown")
    task = admit(client, request)

    class Gateway:
        calls = 0

        def validate(self, *_args):
            return {}

        def submit(self, *_args, **_kwargs):
            self.calls += 1
            raise GenerationError("acceptance_unknown", accepted_unknown=True)

    gateway = Gateway()
    executor = GenerationExecutionService(identity_app[1], identity_app[2], gateway, None)
    executor.execute(task["id"], 1)
    executor.execute(task["id"], 1)
    current = client.get(f"{TASKS}/{task['id']}").json()
    assert current["status"] == "failed" and current["errorCode"] == "acceptance_unknown"
    assert current["canResume"] is False and current["canRetry"] is False
    assert client.post(f"{TASKS}/{task['id']}/resume").status_code == 409
    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    assert gateway.calls == 1
    assert records(identity_app, user) == {"tasks": 1, "records": 1, "bindings": 1}
