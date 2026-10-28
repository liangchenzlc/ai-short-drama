"""显式启用的隔离 HTTP/MySQL/MinIO 合同；签名器和供应商替身不算真实验收。"""

import json
import os
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.ai.gateway.client import GenerationGateway
from short_drama.domain import AIGenerationRecord, AsyncTask, User
from short_drama.service.generations.execution.execution import GenerationExecutionService
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    WORKSPACE,
    admit,
    records,
    seed,
)
from tests.integration.canvas.resources.test_canvas_resources import canvas, png, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Explicit isolated MySQL/MinIO enablement is required; no public supplier is called",
    ),
]
SIGNATURE = "X-Amz-Signature=offline-integration-signature"


@pytest.fixture
def public_runtime(resource_app, monkeypatch):
    app, factory, current = resource_app
    settings = current.model_copy(
        update={"minio_public_endpoint": "media.test", "minio_provider_presign_expiry": 1800}
    )
    app.state.settings = app.state.storage.settings = settings
    posts, signs = [], []
    gateway = GenerationGateway(settings)

    def sign(bucket, key):
        signs.append((bucket, key))
        return "https://media.test/isolated-reference.png?" + SIGNATURE

    def request(method, url, **options):
        assert method == "POST"
        posts.append(deepcopy(options["body"]))
        return (
            200,
            {"Content-Type": "application/json"},
            json.dumps({"video_id": "offline-isolated-video", "status": "pending"}).encode(),
        )

    monkeypatch.setattr(app.state.storage, "provider_presigned_get", sign)
    monkeypatch.setattr(gateway.transport, "request", request)
    return SimpleNamespace(app=(app, factory, settings), gateway=gateway, posts=posts, signs=signs)


def saved_reference(client, source):
    response = upload(client, png(), source=source, key=uuid4().hex)
    assert response.status_code == 200, response.text
    return {"id": "frame-1", "storageKey": "resource:" + response.json()["resource"]["id"]}


def video_seed(runtime):
    client, user, project, path, request = seed(
        runtime.app, "agnes_public_" + uuid4().hex[:12], kind="video"
    )
    response = client.post(
        "/api/v1/ai-model-configs",
        json={
            "service_type": "video",
            "name": "离线 Agnes 首帧合同",
            "provider": "openai",
            "model_key": "agnes-video-2.5-flash",
            "base_url": "https://supplier.test/v1",
            "apikey": "offline-integration-key",
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": "openai-video"},
        },
    )
    assert response.status_code == 201, response.text
    model = response.json()
    workspace = client.get(WORKSPACE).json()
    selection = f"host-{model['id']}::{model['model_key']}"
    selected = client.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "videoModel": selection,
                "model": selection,
            },
        },
    )
    assert selected.status_code == 200, selected.text
    request.update(logicalModelId=model["id"], model=model["model_key"], operation="image_to_video")
    request["input"]["referenceImages"] = [saved_reference(client, request["projectId"])]
    request["input"]["metadata"]["videoStartFrameNodeId"] = "frame-1"
    return client, user, project, path, request


def test_owned_reference_admits_executes_once_and_durable_data_never_contains_signature(
    public_runtime,
):
    runtime = public_runtime
    client, user, _, _, request = video_seed(runtime)
    task = admit(client, request)
    app, factory, settings = runtime.app
    executor = GenerationExecutionService(factory, settings, runtime.gateway, app.state.storage)
    executor.execute(task["id"], 1)
    executor.execute(task["id"], 1)
    assert len(runtime.posts) == len(runtime.signs) == 1
    assert runtime.posts[0]["mode"] == "keyframe"
    assert runtime.posts[0]["first_frame"].endswith(SIGNATURE)
    with factory() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert record.status == "sent"
        assert record.provider_task_id == "offline-isolated-video"
        current = session.get(AsyncTask, int(task["id"]))
        assert current.next_action == "poll" and current.message_version == 2
        frozen = record.request_data["canvas_request"]["input"]["referenceImages"][0]
        assert frozen["storageKey"] == request["input"]["referenceImages"][0]["storageKey"]
        assert frozen["type"] == "image/png" and frozen["width"] == 37
        assert SIGNATURE not in json.dumps([record.request_data, record.response_data])
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


@pytest.mark.parametrize("failure", ["disabled-public", "other-project", "other-private"])
def test_api_rejection_leaves_zero_tasks_or_supplier_calls(public_runtime, failure):
    runtime = public_runtime
    client, user, _, path, request = video_seed(runtime)
    if failure == "disabled-public":
        settings = runtime.app[2].model_copy(
            update={"minio_public_endpoint": None, "minio_provider_presign_expiry": None}
        )
        runtime.app[0].state.settings = settings
    elif failure == "other-project":
        _, _, document = canvas(client, "agnes-other-" + uuid4().hex)
        request["input"]["referenceImages"] = [saved_reference(client, document["id"])]
    else:
        other, _ = account(runtime.app, "agnes_private_" + uuid4().hex[:12])
        request["input"]["referenceImages"] = [saved_reference(other, None)]
    before = client.get(path + "/my-document").json()
    response = client.post(TASKS, json=request)
    assert response.status_code == (422 if failure == "disabled-public" else 404), response.text
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert client.get(path + "/my-document").json() == before
    assert runtime.posts == runtime.signs == []


def test_revoked_author_never_reaches_signer_or_supplier(public_runtime):
    runtime = public_runtime
    client, user, _, _, request = video_seed(runtime)
    task = admit(client, request)
    app, factory, settings = runtime.app
    with factory.begin() as session:
        session.get(User, int(user["id"])).email_verified_at = None
    executor = GenerationExecutionService(factory, settings, runtime.gateway, app.state.storage)
    executor.execute(task["id"], 1)
    assert runtime.posts == runtime.signs == []
