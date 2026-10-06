"""真实账号/MySQL/执行器/本机 HTTP；不访问付费模型。"""

import json
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from short_drama.ai import GenerationGateway
from short_drama.ai.canvas_credentials import decode_canvas_credentials
from short_drama.core.crypto import KeyCipher
from short_drama.domain import AIGenerationRecord, AIModelConfig, AsyncTask, CanvasTaskBinding
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_identity_collaboration import account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
TESTS = "/api/v1/canvas-runtime/model-tests"


def request(base_url="https://provider.example/v1", **changes):
    return {
        "channel": {
            "id": "unsaved-draft",
            "name": "测试渠道",
            "baseUrl": base_url,
            "apiKey": "synthetic-model-test-key",
            "apiFormat": "openai",
            "models": ["gpt-test"],
            "modelProfiles": [
                {"model": "gpt-test", "capability": "text", "protocol": "chat-completion"}
            ],
        },
        "mode": "text",
        "model": "gpt-test",
        "prompt": "Reply with OK.",
        "config": {},
        "textOptions": {"stream": False, "thinking": False},
        "clientOperationId": uuid4().hex,
        **changes,
    }


def create(client, body):
    return client.post(TESTS, json=body, headers={"Idempotency-Key": body["clientOperationId"]})


def test_unsaved_test_admission_is_private_idempotent_and_never_stores_plaintext(identity_app):
    client, _ = account(identity_app, "model_test_admission")
    body = request()
    accepted = create(client, body)
    assert accepted.status_code == 202, accepted.text
    identifier = accepted.json()["id"]
    assert create(client, body).status_code == 200
    changed = deepcopy(body)
    changed["channel"]["apiKey"] = "different-secret"
    assert create(client, changed).status_code == 409
    with identity_app[1]() as session:
        task = session.get(AsyncTask, int(identifier))
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        model = session.get(AIModelConfig, record.config_id)
        assert task.project_id is None and task.scope_user_id == task.initiated_by
        assert model.is_deleted and not model.enabled
        assert "synthetic-model-test-key" not in json.dumps(record.request_data)
        assert "synthetic-model-test-key" not in json.dumps(record.config_snapshot)
        decoded = decode_canvas_credentials(
            record.config_snapshot,
            record.request_data,
            KeyCipher(identity_app[2].encryption_key.get_secret_value()).decrypt(
                record.credential_cipher
            ),
        )
        assert decoded.api_key.get_secret_value() == "synthetic-model-test-key"
        assert session.scalar(select(func.count()).select_from(CanvasTaskBinding)) == 0
    assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
    listed = client.get("/api/v1/canvas-runtime/tasks").json()
    assert [item["id"] for item in listed] == [identifier]
    assert "projectId" not in listed[0]
    assert listed[0]["clientContext"] == {"source": "model-connection-test"}
    assert "synthetic-model-test-key" not in json.dumps(listed)
    assert (
        client.get("/api/v1/canvas-runtime/tasks", params={"sourceNodeId": "any-node"}).json() == []
    )
    other, _ = account(identity_app, "model_test_other")
    assert other.get(f"{TESTS}/{identifier}").status_code == 404
    assert other.post(f"{TESTS}/{identifier}/cancel").status_code == 404
    assert other.get(f"/api/v1/canvas-runtime/tasks/{identifier}").status_code == 404
    assert other.get(f"/api/v1/canvas-runtime/tasks/{identifier}/logs").status_code == 404
    assert other.get("/api/v1/canvas-runtime/tasks").json() == []
    assert client.get("/api/v1/ai-model-configs").json()["items"] == []
    cancelled = client.post(f"/api/v1/canvas-runtime/tasks/{identifier}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"


def test_model_test_unsupported_protocol_rolls_back_all_rows(identity_app):
    client, _ = account(identity_app, "model_test_unsupported")
    body = request()
    body["channel"]["modelProfiles"][0]["protocol"] = "plugin/unsupported"
    rejected = create(client, body)
    assert rejected.status_code == 422
    with identity_app[1]() as session:
        for model in (AsyncTask, AIGenerationRecord, AIModelConfig):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_legacy_credential_references_are_rejected_by_discovery_and_model_test_http(identity_app):
    client, _ = account(identity_app, "model_test_legacy_reference")
    body = request()
    body["channel"].update(id="old-channel", apiKey="", credentialRef="host:old-channel")
    tested = create(client, body)
    assert tested.status_code == 409, tested.text
    assert tested.json()["error"]["code"] == "canvas_model_legacy_credential_reference"
    discovered = client.post(
        "/api/v1/canvas-runtime/ai/models",
        json={
            "baseUrl": body["channel"]["baseUrl"],
            "channelId": "old-channel",
            "credentialRef": "host:old-channel",
        },
    )
    assert discovered.status_code == 409, discovered.text
    assert discovered.json()["error"]["code"] == "canvas_model_legacy_credential_reference"
    with identity_app[1]() as session:
        for model in (AsyncTask, AIGenerationRecord, AIModelConfig):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_model_test_reads_saved_host_key_headers_and_ignores_forged_draft(identity_app):
    client, _ = account(identity_app, "model_test_saved")
    body = request()
    saved = client.post(
        "/api/v1/ai-model-configs",
        json={
            "service_type": "text",
            "name": "已保存模型",
            "provider": "fixture",
            "model_key": body["model"],
            "base_url": body["channel"]["baseUrl"],
            "apikey": "synthetic-model-test-key",
            "headers": [{"name": "X-Saved", "value": "saved-private-header"}],
            "runtime_profile": {
                "version": 1,
                "api_format": "openai",
                "protocol": "chat-completion",
            },
        },
    )
    assert saved.status_code == 201, saved.text
    identifier = saved.json()["id"]
    projection_response = client.get("/api/v1/canvas-runtime/workspace/model-config")
    assert projection_response.status_code == 200, projection_response.text
    projection = projection_response.json()
    assert projection["channels"] == []
    assert projection["models"][0]["has_api_key"]
    body["channel"].update(
        id=f"host-{identifier}",
        baseUrl=f"/api/v1/canvas-runtime/ai/models/{identifier}",
        apiKey="forged-draft-key",
        credentialRef=f"host:{identifier}",
        headers=[{"name": "X-Saved", "value": "forged-draft-header"}],
    )
    body["channel"]["modelProfiles"][0]["protocol"] = "plugin/forged"
    accepted = create(client, body)
    assert accepted.status_code == 202, accepted.text
    with identity_app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(
                AIGenerationRecord.task_id == int(accepted.json()["id"])
            )
        )
        original = session.get(AIModelConfig, int(identifier))
        assert original.enabled and not original.is_deleted
        assert original.row_version == int(saved.json()["row_version"])
        assert original.capability_cache is None
        assert record.config_id != original.id and record.adapter == "openai_chat.v1"
        shadow = session.get(AIModelConfig, record.config_id)
        assert not shadow.enabled and shadow.is_deleted
        decoded = decode_canvas_credentials(
            record.config_snapshot,
            record.request_data,
            KeyCipher(identity_app[2].encryption_key.get_secret_value()).decrypt(
                record.credential_cipher
            ),
        )
        assert decoded.api_key.get_secret_value() == "synthetic-model-test-key"
        assert {item.name: item.value.get_secret_value() for item in decoded.headers} == {
            "X-Saved": "saved-private-header"
        }
        assert "forged-draft" not in json.dumps(record.config_snapshot)
    visible = client.get("/api/v1/ai-model-configs")
    assert visible.status_code == 200, visible.text
    assert [item["id"] for item in visible.json()["items"]] == [identifier]
    after = client.get("/api/v1/canvas-runtime/workspace/model-config")
    assert after.status_code == 200, after.text
    assert after.json() == projection
    altered = deepcopy(body)
    altered["channel"]["baseUrl"] = "https://different.example/v1"
    altered["clientOperationId"] = uuid4().hex
    assert create(client, altered).status_code == 409


def test_host_model_test_accepts_exact_readonly_projection_and_rejects_other_users(identity_app):
    client, _ = account(identity_app, "model_test_host")
    created = client.post(
        "/api/v1/ai-model-configs",
        json={
            "service_type": "text",
            "name": "宿主文本模型",
            "provider": "openai",
            "model_key": "gpt-test",
            "base_url": "https://provider.example/v1",
            "apikey": "host-only-model-test-key",
        },
    )
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    body = request()
    body["channel"].update(
        id=f"host-{identifier}",
        baseUrl=f"/api/v1/canvas-runtime/ai/models/{identifier}",
        apiKey="",
        credentialRef=f"host:{identifier}",
        hasApiKey=True,
        scope="system",
    )
    accepted = create(client, body)
    assert accepted.status_code == 202, accepted.text
    other, _ = account(identity_app, "model_test_foreign")
    assert create(other, body).status_code == 404
    altered = deepcopy(body)
    altered["channel"]["baseUrl"] = "https://different.example/v1"
    altered["clientOperationId"] = uuid4().hex
    assert create(client, altered).status_code == 409


@pytest.mark.parametrize("change", ["version", "credential", "marker", "scene"])
def test_frozen_test_configuration_requires_unchanged_private_server_identity(identity_app, change):
    client, _ = account(identity_app, "model_test_guard")
    accepted = create(client, request())
    assert accepted.status_code == 202, accepted.text
    identifier = accepted.json()["id"]
    with identity_app[1].begin() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(identifier))
        )
        if change == "version":
            session.get(AIModelConfig, record.config_id).row_version += 1
        elif change == "credential":
            record.config_snapshot = {**record.config_snapshot, "credential_identity": "changed"}
        elif change == "marker":
            record.config_snapshot = {**record.config_snapshot, "canvas_model_test": False}
        else:
            record.request_data = {**record.request_data, "source": {"scene": "canvas_node"}}
            record.config_snapshot = {**record.config_snapshot, "canvas_auth_scene": "canvas_node"}
    GenerationExecutionService(
        identity_app[1], identity_app[2], GenerationGateway(identity_app[2]), None
    ).execute(identifier, 1)
    with identity_app[1]() as session:
        task = session.get(AsyncTask, int(identifier))
        assert task.status == "failed" and task.error["code"] == "configuration_disabled"
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(identifier))
        )
        assert record.status == "prepared"


def test_model_test_real_worker_http_response_is_required_for_success(identity_app):
    calls = []

    class Supplier(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.loads(raw)
            calls.append((self.path, payload))
            assert self.headers["Authorization"] == "Bearer synthetic-model-test-key"
            response = json.dumps(
                {
                    "choices": [
                        {"message": {"content": "OK from actual HTTP"}, "finish_reason": "stop"}
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        identity_app[2].model_discovery_allowed_hosts = ["127.0.0.1"]
        client, _ = account(identity_app, "model_test_worker")
        body = request(f"http://127.0.0.1:{server.server_port}/v1")
        accepted = create(client, body)
        assert accepted.status_code == 202, accepted.text
        identifier = accepted.json()["id"]
        assert accepted.json()["status"] == "queued" and "result" not in accepted.json()
        executor = GenerationExecutionService(
            identity_app[1], identity_app[2], GenerationGateway(identity_app[2]), None
        )
        executor.execute(identifier, 1)
        completed = client.get(f"{TESTS}/{identifier}")
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "succeeded", completed.text
        assert completed.json()["result"]["text"] == "OK from actual HTTP"
        task_detail = client.get(f"/api/v1/canvas-runtime/tasks/{identifier}")
        assert task_detail.status_code == 200, task_detail.text
        assert task_detail.json()["resultState"] == "READY"
        assert json.loads(task_detail.json()["resultJson"])["text"] == "OK from actual HTTP"
        assert client.get(f"/api/v1/canvas-runtime/tasks/{identifier}/logs").json()
        assert client.get("/api/v1/canvas-runtime/tasks", params={"activeOnly": True}).json() == []
        assert len(calls) == 1 and calls[0][0] == "/v1/chat/completions"
        assert calls[0][1]["stream"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
