"""三类固定源视频合同经真实 HTTP、隔离 MySQL、执行器及 MinIO 验证。"""

import base64
import gc
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.ai import GenerationGateway
from short_drama.ai.canvas_credentials import decode_canvas_credentials
from short_drama.ai.canvas_video_adapters import (
    BEEFAPI_SEEDANCE,
    NEWAPI_VIDEO_GENERATIONS,
    OPENAI_VIDEOS,
)
from short_drama.core.crypto import KeyCipher
from short_drama.dao.canvas_model_catalog_dao import CanvasModelCatalogDAO
from short_drama.dao.task_runtime_dao import finish
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasTaskBinding,
    MediaFile,
)
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.base import utcnow
from short_drama.service.canvas_beefapi_service import tick_beefapi_connections
from short_drama.service.canvas_generation_service import CanvasGenerationService
from short_drama.service.canvas_model_catalog_service import CanvasModelCatalogService
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_generation_media_runtime import playable_fixture
from tests.integration.test_canvas_generation_runtime import TASKS, admit, bind, records, seed
from tests.integration.test_canvas_resources import canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_workspace import actor
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated video HTTP/MySQL/MinIO verification",
    ),
]
KEY = "canvas-video-protocol-placeholder-not-a-real-key"
HEADER_NAME = "X-Provider-Fixture-Key"
HEADER_VALUE = "canvas-video-protocol-placeholder-private-header"
PROVIDER_ID = "original-canvas-video"
WORKSPACE = "/api/v1/canvas-runtime/workspace/model-config"
CONNECTION = "/api/v1/canvas-runtime/beefapi/connection"


@pytest.fixture
def video_runtime(resource_app, tmp_path):
    app, factory, current = resource_app
    media = playable_fixture(tmp_path, "video")
    calls, errors = [], []
    state = {
        "adapter": OPENAI_VIDEOS,
        "poll": "completed",
        "create": "accepted",
        "uploads": "success",
        "sessions": [],
        "uploaded": {},
    }

    class Supplier(BaseHTTPRequestHandler):
        def handle_request(self):
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if "multipart/form-data" in self.headers.get("Content-Type", ""):
                    parsed = BytesParser(policy=policy.default).parsebytes(
                        ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode()
                        + raw
                    )
                    body = {}
                    for part in parsed.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        data = part.get_payload(decode=True)
                        body[name] = (
                            {
                                "filename": part.get_filename(),
                                "mime": part.get_content_type(),
                                "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                            if part.get_filename()
                            else data.decode()
                        )
                else:
                    body = json.loads(raw or b"{}") if self.command != "PUT" else None
                authenticated = self.headers.get("Authorization") == "Bearer " + KEY
                custom = self.headers.get(HEADER_NAME) == HEADER_VALUE
                calls.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": body,
                        "authenticated": authenticated,
                        "custom_header": custom,
                        "observed_at": time.monotonic(),
                    }
                )
                if self.path.endswith("/device/code"):
                    return self.respond(
                        {
                            "device_code": "video-protocol-controlled-grant",
                            "user_code": "VIDEO-1234",
                            "verification_uri": state["origin"] + "/desktop-auth",
                            "interval": 5,
                            "expires_in": 900,
                        }
                    )
                if self.path.endswith("/device/token"):
                    return self.respond(
                        {
                            "api_key": KEY,
                            "base_url": state["origin"] + "/v1",
                            "market": "enterprise",
                            "group": "enterprise",
                            "token_id": 9007199254740997,
                            "account": {"id": 9007199254740995, "display_name": "协议验收"},
                        }
                    )
                if self.path.endswith("/device/complete"):
                    return self.respond({"success": True})
                if self.path == "/v1/models":
                    assert authenticated
                    return self.respond({"data": [{"id": "seedance-2.5", "model_type": "video"}]})
                if self.path == "/v1/beeftv/connection":
                    assert authenticated
                    return self.respond({})
                if self.command == "PUT" and self.path.startswith("/controlled-upload/"):
                    assert not self.headers.get("Authorization")
                    assert self.headers.get(HEADER_NAME) == "signed-required-provider-header"
                    assert self.headers.get("X-Upload-Fixture") == "signed-upload"
                    if state["uploads"] == "put-interrupted":
                        return self.disconnect()
                    state["uploaded"][self.path.rsplit("/", 1)[1]] = raw
                    return self.respond({})
                assert authenticated and custom, "Frozen private credential was not preserved"
                if self.path == "/v1/video-references/uploads":
                    index = len(state["sessions"])
                    state["sessions"].append(body)
                    if index == 0 and state["uploads"] in {"404", "501"}:
                        return self.respond({}, int(state["uploads"]))
                    if index == 1 and state["uploads"] == "second-404":
                        return self.respond({}, 404)
                    return self.respond(
                        {
                            "ticket": str(index),
                            "upload_url": state["origin"] + f"/controlled-upload/{index}",
                            "upload_method": "PUT",
                            "required_headers": {
                                "X-Upload-Fixture": "signed-upload",
                                "Authorization": "must-not-be-forwarded",
                                HEADER_NAME: "signed-required-provider-header",
                            },
                        }
                    )
                if self.path == "/v1/video-references/uploads/complete":
                    original = state["sessions"][int(body["ticket"])]
                    data = state["uploaded"][body["ticket"]]
                    assert len(data) == original["bytes"]
                    assert hashlib.sha256(data).hexdigest() == original["sha256"]
                    result = {
                        **original,
                        "url": state["origin"] + "/uploaded-reference/" + body["ticket"],
                    }
                    if state["uploads"] == "bad-complete":
                        result["sha256"] = "0" * 64
                    return self.respond(result)
                create_path = (
                    "/v1/video/generations"
                    if state["adapter"] == NEWAPI_VIDEO_GENERATIONS
                    else "/v1/videos"
                )
                if self.command == "POST" and self.path == create_path:
                    if state["create"] == "lost-ack":
                        return self.disconnect()
                    return self.respond({"id": PROVIDER_ID, "status": "queued"})
                if self.command == "GET" and self.path == create_path + "/" + PROVIDER_ID:
                    sequence = state.get("poll_sequence") or []
                    observation = sequence.pop(0) if sequence else None
                    if observation == "malformed":
                        return self.respond(b"invalid-json")
                    if isinstance(observation, dict) and observation.get("http_status"):
                        return self.respond(
                            {},
                            observation["http_status"],
                            headers=observation.get("headers"),
                        )
                    observed_status = observation or state["poll"]
                    if state["adapter"] == NEWAPI_VIDEO_GENERATIONS:
                        return self.respond(
                            {
                                "data": {
                                    "task_id": PROVIDER_ID,
                                    "status": observed_status,
                                    "result_url": state["origin"] + "/movie.mp4",
                                }
                            }
                        )
                    return self.respond({"id": PROVIDER_ID, "status": observed_status})
                if self.path in {"/v1/videos/" + PROVIDER_ID + "/content", "/movie.mp4"}:
                    sequence = state.get("download_sequence") or []
                    failure = sequence.pop(0) if sequence else None
                    if failure:
                        return self.respond({}, failure)
                    return self.respond(media, mime="video/mp4")
                self.send_error(404)
            except BaseException as error:
                errors.append(type(error).__name__)
                self.close_connection = True

        do_POST = do_GET = do_PUT = handle_request

        def respond(self, value, status=200, mime="application/json", headers=None):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def disconnect(self):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    settings = current.model_copy(
        update={
            "generation_allowed_hosts": ["127.0.0.1"],
            "model_discovery_allowed_hosts": ["127.0.0.1"],
            "canvas_beefapi_test_origin": state["origin"],
        }
    )
    app.state.settings = settings
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            app=(app, factory, settings),
            state=state,
            calls=calls,
            media=media,
            gateway=GenerationGateway(settings),
        )
        assert errors == [], errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive(), "Task-owned video supplier was not stopped"


def protocol_seed(runtime, adapter):
    runtime.state["adapter"] = adapter
    client, user, project, path, request = seed(runtime.app, "video_protocol_author", kind="video")
    if adapter == BEEFAPI_SEEDANCE:
        response = client.post(CONNECTION + "/start", json={})
        assert response.status_code == 200 and response.json()["state"] == "pending"
        assert tick_beefapi_connections(runtime.app[1], runtime.app[2]) == 1
        assert client.get(CONNECTION).json()["state"] == "connected"
    current = client.get(WORKSPACE).json()
    channels = current["channels"]
    if adapter == BEEFAPI_SEEDANCE:
        channel = next(item for item in channels if item["id"] == "beefapi")
        channel["headers"] = [{"name": HEADER_NAME, "value": HEADER_VALUE}]
        model = "seedance-2.5"
    else:
        model, protocol = (
            ("sora", "openai-videos")
            if adapter == OPENAI_VIDEOS
            else ("custom-video", "newapi-channel-2")
        )
        channels.append(
            {
                "id": "protocol-provider",
                "name": "受控视频协议渠道",
                "baseUrl": runtime.state["origin"] + "/v1",
                "apiKey": KEY,
                "headers": [{"name": HEADER_NAME, "value": HEADER_VALUE}],
                "apiFormat": "openai",
                "models": [model],
                "modelProfiles": [{"model": model, "capability": "video", "protocol": protocol}],
            }
        )
    saved = client.put(
        WORKSPACE,
        json={
            "expected_row_version": current["row_version"],
            "preferences": current["preferences"],
            "channels": channels,
        },
    )
    assert saved.status_code == 200, saved.text
    channel_key = "beefapi" if adapter == BEEFAPI_SEEDANCE else "protocol-provider"
    channel = next(item for item in saved.json()["channels"] if item["id"] == channel_key)
    profile = next(item for item in channel["modelProfiles"] if item["model"] == model)
    request["logicalModelId"], request["model"] = profile["logicalModelId"], model
    request["input"]["config"] = {
        "size": "16:9",
        "videoSeconds": "5",
        "vquality": "720p",
        "videoGenerateAudio": "true",
    }
    with runtime.app[1]() as session:
        config = session.get(AIModelConfig, int(request["logicalModelId"]))
        assert config.capability_cache["adapter"] == adapter
    return client, user, project, path, request


@pytest.mark.parametrize("adapter", [OPENAI_VIDEOS, NEWAPI_VIDEO_GENERATIONS, BEEFAPI_SEEDANCE])
@pytest.mark.parametrize("legacy_cache", ["missing", "old_adapter", "expired"])
def test_saved_catalog_upgrades_before_direct_admission_without_resave_or_reconnect(
    video_runtime, adapter, legacy_cache
):
    runtime = video_runtime
    client, user, _, _, request = protocol_seed(runtime, adapter)
    workspace = client.get(WORKSPACE).json()
    identifier = int(request["logicalModelId"])
    with runtime.app[1].begin() as session:
        config = session.get(AIModelConfig, identifier)
        expected_cache = deepcopy(config.capability_cache)
        original_version = config.row_version
        original_identity = (
            config.id,
            config.owner_user_id,
            config.base_url,
            config.model_key,
            config.provider,
            config.apikey,
        )
        stale = deepcopy(expected_cache)
        stale["adapter"] = "ark_video.v1"
        if legacy_cache == "expired":
            stale["fingerprint"] = "0" * 64
        if legacy_cache == "missing":
            stale = None
        config.capability_cache = stale

    assert client.get(WORKSPACE).json() == workspace, "GET must remain read-only"
    with runtime.app[1]() as session:
        config = session.get(AIModelConfig, identifier)
        assert config.capability_cache == stale and config.row_version == original_version

    task = admit(client, request)
    assert client.get(WORKSPACE).json() == workspace
    with runtime.app[1]() as session:
        config = session.get(AIModelConfig, identifier)
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == int(task["id"]))
        )
        assert config.capability_cache == expected_cache
        assert config.row_version == original_version + 1
        assert (
            config.id,
            config.owner_user_id,
            config.base_url,
            config.model_key,
            config.provider,
            config.apikey,
        ) == original_identity
        assert call.config_snapshot["row_version"] == str(config.row_version)
        assert call.adapter == adapter
        frozen_call = (
            deepcopy(call.config_snapshot),
            deepcopy(call.request_data),
            call.credential_cipher,
            deepcopy(binding.source_snapshot),
        )

    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    fresh = deepcopy(request)
    fresh["input"]["metadata"]["clientOperationId"] = uuid4().hex
    second = admit(client, fresh)
    assert second["id"] != task["id"]
    with runtime.app[1]() as session:
        config = session.get(AIModelConfig, identifier)
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == int(task["id"]))
        )
        assert config.row_version == original_version + 1
        assert (
            call.config_snapshot,
            call.request_data,
            call.credential_cipher,
            binding.source_snapshot,
        ) == frozen_call
    assert client.get(WORKSPACE).json() == workspace
    assert records(runtime.app, user) == {"tasks": 2, "records": 2, "bindings": 2}
    assert not any(
        item["path"] in {"/v1/videos", "/v1/video/generations"} for item in runtime.calls
    )


@pytest.mark.parametrize("adapter", [OPENAI_VIDEOS, NEWAPI_VIDEO_GENERATIONS, BEEFAPI_SEEDANCE])
def test_disabled_saved_model_replays_original_task_without_refresh_or_reactivation(
    video_runtime, adapter
):
    runtime = video_runtime
    client, user, _, _, request = protocol_seed(runtime, adapter)
    task = admit(client, request)
    identifier = int(request["logicalModelId"])
    with runtime.app[1].begin() as session:
        config = session.get(AIModelConfig, identifier)
        config.enabled, config.capability_cache = 0, None
        config.row_version += 1
        version = config.row_version
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        frozen = (
            deepcopy(call.config_snapshot),
            deepcopy(call.request_data),
            call.credential_cipher,
        )

    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    fresh = deepcopy(request)
    fresh["input"]["metadata"]["clientOperationId"] = uuid4().hex
    rejected = client.post(TASKS, json=fresh)
    assert rejected.status_code == 400, rejected.text
    with runtime.app[1]() as session:
        config = session.get(AIModelConfig, identifier)
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert not config.enabled and config.capability_cache is None
        assert config.row_version == version
        assert (call.config_snapshot, call.request_data, call.credential_cipher) == frozen
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


@pytest.mark.parametrize("adapter", [OPENAI_VIDEOS, NEWAPI_VIDEO_GENERATIONS, BEEFAPI_SEEDANCE])
def test_catalog_save_and_admission_lock_in_the_same_order_and_freeze_one_snapshot(
    video_runtime, adapter, monkeypatch
):
    runtime = video_runtime
    client, user, _, _, request = protocol_seed(runtime, adapter)
    current = client.get(WORKSPACE).json()
    channel_key = "beefapi" if adapter == BEEFAPI_SEEDANCE else "protocol-provider"
    changed = deepcopy(current["channels"])
    edited = next(item for item in changed if item["id"] == channel_key)
    edited["headers"] = [{"name": HEADER_NAME, "value": HEADER_VALUE + "-new"}]
    identifier = int(request["logicalModelId"])
    with runtime.app[1].begin() as session:
        config = session.get(AIModelConfig, identifier)
        config.capability_cache = None
        initial_version = config.row_version

    save_has_catalog = threading.Event()
    admission_requests_catalog = threading.Event()
    lock_order = []
    original_catalog = CanvasModelCatalogDAO.catalog
    original_models = CanvasModelCatalogDAO.models
    original_config = AIGenerationService._config
    original_refresh = CanvasModelCatalogService.refresh_runtime_model_locked

    def catalog(dao, user_id, *, lock=False):
        operation = dao.session.info.get("runtime_test_operation")
        if operation == "admit" and lock:
            admission_requests_catalog.set()
        result = original_catalog(dao, user_id, lock=lock)
        if operation == "save" and lock:
            save_has_catalog.set()
        if operation == "admit" and lock:
            lock_order.append("admit_catalog")
        return result

    def models(dao, identifiers, *, lock=False):
        if dao.session.info.get("runtime_test_operation") == "save" and lock:
            assert admission_requests_catalog.wait(10), "Admission never attempted catalog lock"
        result = original_models(dao, identifiers, lock=lock)
        if dao.session.info.get("runtime_test_operation") == "save" and lock:
            lock_order.append("save_models")
        return result

    def model(service, kind, identifier):
        result = original_config(service, kind, identifier)
        if service.session.info.get("runtime_test_operation") == "admit":
            lock_order.append("admit_model")
        return result

    def refresh(service, config):
        result = original_refresh(service, config)
        # Catalog is a weak identity-map entry; credentials must use a current read.
        gc.collect()
        return result

    monkeypatch.setattr(CanvasModelCatalogDAO, "catalog", catalog)
    monkeypatch.setattr(CanvasModelCatalogDAO, "models", models)
    monkeypatch.setattr(AIGenerationService, "_config", model)
    monkeypatch.setattr(CanvasModelCatalogService, "refresh_runtime_model_locked", refresh)

    def save_catalog():
        with runtime.app[1]() as session:
            session.info.update(actor=actor(int(user["id"])), runtime_test_operation="save")
            return CanvasWorkspaceService(session, settings=runtime.app[2]).save_preferences(
                CanvasWorkspacePreferencesRequest(
                    expected_row_version=current["row_version"],
                    preferences=current["preferences"],
                    channels=changed,
                )
            )

    def admit_task():
        with runtime.app[1]() as session:
            session.info.update(actor=actor(int(user["id"])), runtime_test_operation="admit")
            return CanvasGenerationService(session, runtime.app[2]).create(
                CanvasRuntimeTaskCreate.model_validate(request)
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        saving = executor.submit(save_catalog)
        assert save_has_catalog.wait(10), "Save never acquired catalog lock"
        admitting = executor.submit(admit_task)
        saved = saving.result(timeout=20)
        task, created = admitting.result(timeout=20)
    assert created
    assert lock_order.index("save_models") < lock_order.index("admit_catalog")
    assert lock_order.index("admit_catalog") < lock_order.index("admit_model")
    assert saved["row_version"] == str(int(current["row_version"]) + 1)
    with runtime.app[1]() as session:
        config = session.get(AIModelConfig, identifier)
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        assert config.row_version == initial_version + 1
        assert config.capability_cache["adapter"] == call.adapter == adapter
        assert call.config_snapshot["row_version"] == str(config.row_version)
        credentials = decode_canvas_credentials(
            call.config_snapshot,
            call.request_data,
            KeyCipher(runtime.app[2].encryption_key.get_secret_value()).decrypt(
                call.credential_cipher
            ),
        )
        headers = {item.name: item.value.get_secret_value() for item in credentials.headers}
        assert headers[HEADER_NAME] == HEADER_VALUE + "-new"
        assert call.request_data["source"]["scene"] == "canvas_node"
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


def execute(runtime, task, *, steps=6):
    executor = GenerationExecutionService(
        runtime.app[1], runtime.app[2], runtime.gateway, runtime.app[0].state.storage
    )
    actions = []
    for _ in range(steps):
        with runtime.app[1].begin() as session:
            current = session.get(AsyncTask, int(task["id"]))
            if current.status in {"succeeded", "failed", "cancelled"} or not current.next_action:
                break
            version, action = current.message_version, current.next_action
            # 仅推进当前测试任务到 due，保留生产 next_action/version 和周期值。
            if current.next_run_at and current.next_run_at > utcnow():
                current.next_run_at = utcnow()
        actions.append(action)
        executor.execute(task["id"], version)
    return executor, actions


def submissions(runtime):
    return [
        item
        for item in runtime.calls
        if item["method"] == "POST" and item["path"] in {"/v1/videos", "/v1/video/generations"}
    ]


def local_image(client, request, *, key=None):
    content = png("purple")
    response = upload(client, content, key=key or uuid4().hex, source=request["projectId"])
    assert response.status_code == 200, response.text
    resource = response.json()["resource"]
    return {
        "id": "saved-reference-" + uuid4().hex,
        "name": "已保存参考图",
        "storageKey": "resource:" + resource["id"],
        "type": "image/png",
        "bytes": 999999,
        "width": 999,
        "height": 999,
    }, content


@pytest.mark.parametrize("adapter", [OPENAI_VIDEOS, NEWAPI_VIDEO_GENERATIONS, BEEFAPI_SEEDANCE])
def test_protocol_create_poll_download_archive_bind_and_private_scope(video_runtime, adapter):
    runtime = video_runtime
    client, user, project, path, request = protocol_seed(runtime, adapter)
    member, other = account(runtime.app, "video_protocol_reader")
    join(runtime.app, client, member, project["id"], other["id"])
    member.headers["X-Canvas-Actor"] = other["id"]
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    executor, actions = execute(runtime, task)
    assert actions == ["submit", "poll", "save"]
    response = client.get(f"{TASKS}/{task['id']}")
    assert response.status_code == 200, response.text
    completed = response.json()
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    media = json.loads(completed["resultJson"])["video"]
    assert (media["width"], media["height"], media["durationMs"]) == (160, 90, 1000)
    assert media["bytes"] == len(runtime.media) and media["mimeType"] == "video/mp4"
    assert client.get(media["url"]).content == runtime.media
    ranged = client.get(media["url"], headers={"Range": "bytes=4-15"})
    assert ranged.status_code == 206 and ranged.content == runtime.media[4:16]
    assert member.get(media["url"]).status_code == 404
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert member.get(f"{TASKS}/{task['id']}/logs").status_code == 404
    assert client.get(path + "/my-document").json() == before
    with runtime.app[1]() as session:
        call = session.scalar(select(AIGenerationRecord))
        assert call.adapter == adapter and call.provider_task_id == PROVIDER_ID
        media_file = session.scalar(select(MediaFile))
        assert media_file.checksum_sha256 == hashlib.sha256(runtime.media).hexdigest()
        assert media_file.storage_locator.startswith("minio://canvas-test-")
        version = session.get(AsyncTask, int(task["id"])).message_version
    create = submissions(runtime)
    assert len(create) == 1 and create[0]["authenticated"] and create[0]["custom_header"]
    body = create[0]["body"]
    assert body["model"] == request["model"] and body["prompt"] == request["prompt"]
    if adapter == OPENAI_VIDEOS:
        assert body["seconds"] == "5" and body["size"] == "16:9"
        assert body["resolution_name"] == "720p"
    elif adapter == NEWAPI_VIDEO_GENERATIONS:
        assert body["seconds"] == "5" and body["aspect_ratio"] == "16:9"
        assert body["generate_audio"] is True
    else:
        assert body["duration"] == 5 and body["generate_audio"] is True
    expected_poll = create[0]["path"] + "/" + PROVIDER_ID
    assert [item["path"] for item in runtime.calls if item["path"] == expected_poll] == [
        expected_poll
    ]
    expected_download = (
        "/movie.mp4"
        if adapter == NEWAPI_VIDEO_GENERATIONS
        else "/v1/videos/" + PROVIDER_ID + "/content"
    )
    downloaded = [item for item in runtime.calls if item["path"] == expected_download]
    assert len(downloaded) == 1 and downloaded[0]["authenticated"]
    bound = bind(client, request, completed)
    node = bound["result"]["node"]
    assert node["metadata"]["storageKey"] == media["storageKey"]
    assert node["position"] == {"x": 420.25, "y": -20.5}
    assert member.get(media["url"]).content == runtime.media
    fresh = type(client)(runtime.app[0])
    fresh.headers["Origin"] = runtime.app[2].public_origin
    login = fresh.post(
        "/api/v1/auth/login",
        json={"username": "video_protocol_author", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    fresh.headers["X-Canvas-Actor"] = user["id"]
    refreshed = fresh.get(path + "/my-document").json()
    assert refreshed["source_document"]["nodes"][0]["metadata"]["storageKey"] == media["storageKey"]
    executor.execute(task["id"], 1)
    executor.execute(task["id"], version)
    assert len(submissions(runtime)) == 1
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


def test_openai_saved_reference_is_real_multipart_and_frozen_metadata(video_runtime):
    runtime = video_runtime
    client, _, _, _, request = protocol_seed(runtime, OPENAI_VIDEOS)
    reference, content = local_image(client, request)
    request["operation"] = "image_to_video"
    request["input"]["referenceImages"] = [reference]
    task = admit(client, request)
    execute(runtime, task)
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    actual = submissions(runtime)[0]["body"]["input_reference"]
    assert actual == {
        "filename": "input-reference.png",
        "mime": "image/png",
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    with runtime.app[1]() as session:
        frozen = session.scalar(select(AIGenerationRecord)).request_data
    saved = frozen["canvas_request"]["input"]["referenceImages"][0]
    assert (saved["bytes"], saved["width"], saved["height"]) == (len(content), 37, 19)


def test_custom2_url_replacement_reuses_preaccept_operation_without_duplicate_create(video_runtime):
    runtime = video_runtime
    client, user, _, path, request = protocol_seed(runtime, NEWAPI_VIDEO_GENERATIONS)
    reference, _ = local_image(client, request)
    request["operation"] = "reference_to_video"
    request["input"]["referenceImages"] = [reference]
    before = client.get(path + "/my-document").json()
    rejected = client.post(TASKS, json=request)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "reference_media_requires_url"
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert submissions(runtime) == []
    assert client.get(path + "/my-document").json() == before
    operation_key = request["input"]["metadata"]["clientOperationId"]
    request["input"]["referenceImages"] = [
        {
            "id": reference["id"],
            "name": reference["name"],
            "url": "https://public-reference.example/a.png",
        }
    ]
    task = admit(client, request)
    assert request["input"]["metadata"]["clientOperationId"] == operation_key
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    execute(runtime, task)
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert len(submissions(runtime)) == 1
    assert submissions(runtime)[0]["body"]["image_urls"] == [
        "https://public-reference.example/a.png"
    ]
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


@pytest.mark.parametrize("scope", ["another-project", "same-project-private-member"])
def test_saved_reference_scope_is_atomic_404_without_supplier_request(video_runtime, scope):
    runtime = video_runtime
    client, user, project, path, request = protocol_seed(runtime, OPENAI_VIDEOS)
    if scope == "another-project":
        _, _, document = canvas(client, "video-reference-other-project")
        upload_request = {"projectId": document["id"]}
        uploader = client
    else:
        uploader, other = account(runtime.app, "video_reference_private_member")
        join(runtime.app, client, uploader, project["id"], other["id"])
        uploader.headers["X-Canvas-Actor"] = other["id"]
        upload_request = request
    reference, _ = local_image(uploader, upload_request)
    request["operation"] = "image_to_video"
    request["input"]["referenceImages"] = [reference]
    before = client.get(path + "/my-document").json()
    response = client.post(TASKS, json=request)
    assert response.status_code == 404, response.text
    assert client.get(path + "/my-document").json() == before
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert submissions(runtime) == []


@pytest.mark.parametrize("count", [30, 31])
def test_seedance25_thirty_saved_images_boundary(video_runtime, count):
    runtime = video_runtime
    client, user, _, path, request = protocol_seed(runtime, BEEFAPI_SEEDANCE)
    reference, content = local_image(client, request)
    request["operation"] = "reference_to_video"
    request["input"]["referenceImages"] = [
        {**reference, "id": f"reference-{index}"} for index in range(count)
    ]
    before = client.get(path + "/my-document").json()
    response = client.post(TASKS, json=request)
    if count == 31:
        assert response.status_code == 422, response.text
        assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
        assert client.get(path + "/my-document").json() == before
        assert submissions(runtime) == [] and runtime.state["sessions"] == []
        return
    assert response.status_code == 202, response.text
    task = response.json()
    execute(runtime, task)
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert len(submissions(runtime)) == 1
    body = submissions(runtime)[0]["body"]
    assert len(body["content"]) == 30
    assert all(item["role"] == "reference_image" for item in body["content"])
    assert body["metadata"]["omni_reference_task_type"] == "reference"
    assert len(runtime.state["sessions"]) == len(runtime.state["uploaded"]) == 30
    assert all(value == content for value in runtime.state["uploaded"].values())


@pytest.mark.parametrize(
    "mode", ["success", "404", "501", "put-interrupted", "bad-complete", "second-404"]
)
def test_managed_preupload_and_interruption_never_create_early(video_runtime, mode):
    runtime = video_runtime
    runtime.state["uploads"] = mode
    client, _, _, _, request = protocol_seed(runtime, BEEFAPI_SEEDANCE)
    reference, content = local_image(client, request)
    request["operation"] = "reference_to_video"
    request["input"]["referenceImages"] = [reference]
    if mode == "second-404":
        request["input"]["referenceImages"].append({**reference, "id": "second-reference"})
    task = admit(client, request)
    execute(runtime, task)
    current = client.get(f"{TASKS}/{task['id']}").json()
    if mode in {"put-interrupted", "bad-complete", "second-404"}:
        assert current["status"] == "failed", current
        assert submissions(runtime) == []
        assert current["outputs"] == []
        with runtime.app[1]() as session:
            call = session.scalar(select(AIGenerationRecord))
            assert call.provider_task_id is None and call.status == "failed"
        return
    assert current["status"] == "succeeded", current
    assert len(submissions(runtime)) == 1
    actual = submissions(runtime)[0]["body"]["content"][0]["image_url"]["url"]
    if mode == "success":
        assert actual == runtime.state["origin"] + "/uploaded-reference/0"
        assert runtime.state["uploaded"]["0"] == content
    else:
        assert actual == "data:image/png;base64," + base64.b64encode(content).decode()
        assert runtime.state["uploaded"] == {}
    assert len(runtime.state["sessions"]) == 1


@pytest.mark.parametrize("adapter", [OPENAI_VIDEOS, NEWAPI_VIDEO_GENERATIONS, BEEFAPI_SEEDANCE])
def test_query_provider_recovers_same_failed_task_without_new_create(video_runtime, adapter):
    runtime = video_runtime
    client, user, _, _, request = protocol_seed(runtime, adapter)
    task = admit(client, request)
    with runtime.app[1].begin() as session:
        row = session.get(AsyncTask, int(task["id"]))
        call = session.scalar(select(AIGenerationRecord))
        finish(row, "failed", {"code": "poll_failed", "message": "受控原查询失败"})
        call.provider_task_id, call.status = PROVIDER_ID, "failed"
        call.finished_at = call.updated_at = utcnow()
        call.error = {"code": "poll_failed", "message": "受控原查询失败"}
    query_path = f"{TASKS}/{task['id']}/query-provider"
    response = client.post(query_path)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recovered"] is True and result["providerStatus"] == "succeeded"
    assert result["task"]["id"] == task["id"] and result["task"]["status"] == "succeeded"
    assert submissions(runtime) == []
    before = len(runtime.calls)
    assert client.post(query_path).json() == result
    assert len(runtime.calls) == before
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


@pytest.mark.parametrize("adapter", [OPENAI_VIDEOS, NEWAPI_VIDEO_GENERATIONS, BEEFAPI_SEEDANCE])
def test_lost_create_ack_is_unknown_and_same_operation_never_posts_twice(video_runtime, adapter):
    runtime = video_runtime
    runtime.state["create"] = "lost-ack"
    client, user, _, _, request = protocol_seed(runtime, adapter)
    task = admit(client, request)
    executor, _ = execute(runtime, task, steps=1)
    current = client.get(f"{TASKS}/{task['id']}").json()
    assert current["status"] == "failed", current
    assert current["canRetry"] is False and current["canResume"] is False
    with runtime.app[1]() as session:
        call = session.scalar(select(AIGenerationRecord))
        assert call.status == "unknown" and call.provider_task_id is None
        version = session.get(AsyncTask, int(task["id"])).message_version
    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    executor.execute(task["id"], 1)
    executor.execute(task["id"], version)
    assert len(submissions(runtime)) == 1
    retry = deepcopy(request)
    retry["input"]["metadata"].update(clientOperationId=uuid4().hex, retryOf=task["id"])
    rejected = client.post(TASKS, json=retry)
    assert rejected.status_code == 409, rejected.text
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
    reason="Enable real source UI/video/Python/MySQL/MinIO verification",
)
@pytest.mark.parametrize("mode", ["https-replacement", "unknown"])
def test_original_video_ui_preserves_https_decision_refresh_bind_and_unknown(
    video_runtime, tmp_path, mode
):
    runtime = video_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    adapter = NEWAPI_VIDEO_GENERATIONS if mode == "https-replacement" else OPENAI_VIDEOS
    client, user, project, path, request = protocol_seed(runtime, adapter)
    if mode == "unknown":
        runtime.state["create"] = "lost-ack"
    reference = None
    if mode == "https-replacement":
        reference, _ = local_image(client, request)
    current = client.get(WORKSPACE).json()
    preferences = {
        **current["preferences"],
        "model": "protocol-provider::" + request["model"],
        "videoModel": "protocol-provider::" + request["model"],
        "size": "16:9",
        "videoSeconds": "5",
        "vquality": "720p",
        "videoGenerateAudio": "true",
    }
    saved_preferences = client.put(
        WORKSPACE,
        json={"expected_row_version": current["row_version"], "preferences": preferences},
    )
    assert saved_preferences.status_code == 200, saved_preferences.text
    initial = client.get(path + "/my-document").json()
    document = initial["source_document"]
    document["nodes"] = [
        {
            "id": "runtime-node",
            "type": "video",
            "title": "浏览器原视频生成节点",
            "position": {"x": 550, "y": 200},
            "width": 320,
            "height": 220,
            "metadata": {"content": "", "prompt": "尚未生成的视频来源"},
        }
    ]
    document["connections"] = []
    if reference is not None:
        document["nodes"].insert(
            0,
            {
                "id": "browser-saved-reference",
                "type": "image",
                "title": "原图仍保留",
                "position": {"x": 120, "y": 200},
                "width": 320,
                "height": 220,
                "metadata": {"content": "", "storageKey": reference["storageKey"]},
            },
        )
        document["connections"] = [
            {
                "id": "browser-reference-link",
                "fromNodeId": "browser-saved-reference",
                "toNodeId": "runtime-node",
            }
        ]
    response = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "browser-video-" + uuid4().hex},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert response.status_code == 200, response.text
    assert client.post("/api/v1/auth/logout").status_code == 200
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4194"})
    release_path = tmp_path / "original-video-release"
    stopped = threading.Event()
    worker_errors = []
    executor = GenerationExecutionService(
        runtime.app[1], settings, runtime.gateway, runtime.app[0].state.storage
    )

    def run_worker():
        try:
            while not stopped.wait(0.02):
                with runtime.app[1]() as session:
                    pending = list(
                        session.execute(
                            select(
                                AsyncTask.id, AsyncTask.message_version, AsyncTask.next_action
                            ).where(
                                AsyncTask.initiated_by == int(user["id"]),
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                            )
                        )
                    )
                for identifier, version, action in pending:
                    # 精准推进本测试 task 的调度；生产 30 秒 poll 策略保持原值。
                    if action == "poll" and mode != "unknown" and not release_path.exists():
                        continue
                    if stopped.is_set():
                        break
                    executor.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(error)

    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker = threading.Thread(target=run_worker, name="canvas-video-test-driver", daemon=True)
        worker.start()
        config = {
            "apiUrl": api_url,
            "username": "video_protocol_author",
            "password": PASSWORD,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": request["projectId"],
            "mode": mode,
            "releasePath": str(release_path),
            "referenceKey": reference["storageKey"] if reference is not None else None,
            "mediaHash": hashlib.sha256(runtime.media).hexdigest(),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-video-protocols-python.mjs"],
            cwd=frontend,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Video protocol browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            worker.join(5)
        assert not worker.is_alive(), "Task-owned video test driver did not stop"
        assert not worker_errors, worker_errors
        assert process.returncode == 0, f"Video protocol browser failed: {errors}\n{output}"
        result = json.loads(output)
        assert result["source_saved_before_admission"]
        assert result["same_task_after_refresh"] and result["fresh_browser_preserved"]
        if mode == "https-replacement":
            assert result["original_https_dialog"] and result["same_operation_after_replacement"]
            assert result["source_bind_applied"] and result["real_video_decoded"]
            assert len(result["task_posts"]) == 2
        else:
            assert result["unknown_retry_unavailable"] and len(result["task_posts"]) == 1
        assert len(submissions(runtime)) == 1
        assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
        assert result["page_errors"] == []
