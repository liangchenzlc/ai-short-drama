"""固定源音频合同经真实 HTTP、隔离 MySQL、MinIO 与执行器验证。"""

import base64
import hashlib
import json
import os
import socket
import threading
from copy import deepcopy
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.ai import GenerationGateway
from short_drama.ai.canvas_audio_adapters import ASYNC_AUDIO
from short_drama.dao.canvas_generation_dao import CanvasGenerationDAO
from short_drama.dao.task_runtime_dao import finish
from short_drama.domain import AIGenerationRecord, AIModelConfig, AsyncTask, MediaFile
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.canvas.generation.test_canvas_generation_media_runtime import (
    playable_fixture,
)
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    WORKSPACE,
    admit,
    bind,
    records,
    seed,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)
from tests.unit.canvas.audio.test_canvas_audio_formats import MIMES, encoded_audio

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated audio HTTP/MySQL/MinIO verification",
    ),
]
KEY = "canvas-audio-protocol-placeholder-key"
HEADER_NAME = "X-Audio-Fixture"
HEADER_VALUE = "canvas-audio-protocol-placeholder-header"
PROVIDER_ID = "original-canvas-audio"


@pytest.fixture
def audio_runtime(resource_app, tmp_path):
    app, factory, current = resource_app
    media = playable_fixture(tmp_path, "audio")
    calls, errors = [], []
    state = {"protocol": "async-audio", "create": "accepted", "poll": "content", "pending": 0}

    class Supplier(BaseHTTPRequestHandler):
        def handle_request(self):
            try:
                payload_media = state.get("media", media)
                payload_mime = state.get("mime", "audio/mpeg")
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                authenticated = self.headers.get("Authorization") == "Bearer " + KEY
                custom = self.headers.get(HEADER_NAME) == HEADER_VALUE
                calls.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": json.loads(raw or b"{}"),
                        "authenticated": authenticated,
                        "custom_header": custom,
                        "accept": self.headers.get("Accept"),
                    }
                )
                if self.server is cdn:
                    assert not self.headers.get("Authorization") and not self.headers.get(
                        HEADER_NAME
                    )
                    return self.respond(payload_media, mime=payload_mime)
                assert authenticated and custom, "Frozen audio credential was not preserved"
                if self.command == "POST":
                    if state["create"] == "lost-ack":
                        return self.disconnect()
                    if state["create"] == "missing-id":
                        return self.respond({"status": "running"})
                    if state["protocol"] == "openai-audio":
                        assert self.path == "/v1/audio/speech"
                        return self.respond(payload_media, mime=payload_mime)
                    assert self.path == "/v1/audio/tasks"
                    return self.respond({"data": {"id": PROVIDER_ID, "status": "queued"}})
                if self.path == "/v1/audio/tasks/" + PROVIDER_ID + "/content":
                    assert self.headers.get("Accept") == "audio/*"
                    if state.get("download_failure"):
                        return self.respond({}, status=503)
                    return self.respond(payload_media, mime=payload_mime)
                assert self.path == "/v1/audio/tasks/" + PROVIDER_ID
                if state["pending"]:
                    state["pending"] -= 1
                    return self.respond({"status": "processing"})
                if type(state["poll"]) is int:
                    return self.respond({}, status=state["poll"])
                if state["poll"] == "malformed":
                    return self.respond(b"invalid-json")
                if state["poll"] == "disconnect":
                    return self.disconnect()
                if state["poll"] in {"failed", "cancelled"}:
                    return self.respond(
                        {"status": state["poll"], "message": "private-upstream-audio-message"}
                    )
                body = {"status": "completed"}
                if state["poll"] == "url":
                    body["audio_url"] = state["cdn_origin"] + "/sound.mp3"
                if state["poll"] == "inline":
                    body["audio_url"] = (
                        f"data:{payload_mime};base64," + base64.b64encode(payload_media).decode()
                    )
                return self.respond(body)
            except Exception as error:
                errors.append(str(error))
                self.respond({}, status=500)

        def respond(self, value, status=200, mime="application/json"):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()

        def disconnect(self):
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    cdn = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    state["cdn_origin"] = f"http://127.0.0.1:{cdn.server_port}"
    settings = current.model_copy(update={"generation_allowed_hosts": ["127.0.0.1"]})
    app.state.settings = settings
    threads = [threading.Thread(target=item.serve_forever, daemon=True) for item in (server, cdn)]
    for thread in threads:
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
        for item in (server, cdn):
            item.shutdown()
            item.server_close()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive(), "Task-owned audio supplier was not stopped"


def audio_seed(runtime, *, protocol="async-audio", model="compatible-speech", defaults=None):
    runtime.state["protocol"] = protocol
    client, user, project, path, request = seed(runtime.app, "audio_protocol_author", kind="audio")
    saved = client.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
    response = client.patch(
        "/api/v1/ai-model-configs/" + saved["id"],
        json={
            "row_version": saved["row_version"],
            "base_url": runtime.state["origin"] + "/v1",
            "model_key": model,
            "apikey": KEY,
            "headers": [{"name": HEADER_NAME, "value": HEADER_VALUE}],
            "runtime_profile": {
                "version": 1,
                "api_format": "openai",
                "protocol": protocol,
                **({"default_options": defaults} if defaults is not None else {}),
            },
        },
    )
    assert response.status_code == 200, response.text
    request["model"] = response.json()["model_key"]
    request["input"]["config"] = {
        "audioVoice": "nova",
        "audioFormat": "mp3",
        "audioSpeed": "1.25",
        "audioInstructions": "温暖自然的旁白",
    }
    workspace = client.get(WORKSPACE).json()
    selected = client.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "audioModel": f"host-{saved['id']}::{request['model']}",
            },
        },
    )
    assert selected.status_code == 200, selected.text
    return client, user, project, path, request


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
            if current.next_run_at and current.next_run_at > utcnow():
                current.next_run_at = utcnow()
        actions.append(action)
        executor.execute(task["id"], version)
    return executor, actions


def submissions(runtime):
    return [item for item in runtime.calls if item["method"] == "POST"]


@pytest.mark.parametrize("protocol", ["async-audio", "openai-audio"])
def test_audio_projection_sees_media_committed_after_ownership_snapshot(
    audio_runtime, monkeypatch, protocol
):
    runtime = audio_runtime
    client, _, project, path, request = audio_seed(runtime, protocol=protocol)
    member, member_user = account(runtime.app, "audio_snapshot_member")
    task = admit(client, request)
    before = client.get(path).json()
    original_binding = CanvasGenerationDAO.binding
    snapshot_observed = False

    def binding_then_archive(dao, identifier, actor_id):
        nonlocal snapshot_observed
        binding = original_binding(dao, identifier, actor_id)
        if identifier == int(task["id"]) and not snapshot_observed:
            snapshot_observed = True
            # 先由真实一致性读建立旧快照，再由独立Session完成真实HTTP/MinIO归档。
            assert dao.session.scalar(select(MediaFile)) is None
            _, actions = execute(runtime, task)
            assert actions == (
                ["submit", "poll", "save"] if protocol == "async-audio" else ["submit", "save"]
            )
        return binding

    monkeypatch.setattr(CanvasGenerationDAO, "binding", binding_then_archive)
    response = client.get(f"{TASKS}/{task['id']}")
    assert response.status_code == 200, response.text
    completed = response.json()
    assert snapshot_observed
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    media = json.loads(completed["resultJson"])["audio"]
    assert client.get(media["url"]).content == runtime.media
    assert member.get(media["url"]).status_code == 404
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert client.get(path).json() == before
    bound = bind(client, request, completed)
    assert bound["result"]["node"]["metadata"]["storageKey"] == media["storageKey"]
    assert member.get(media["url"]).status_code == 404
    assert client.get(media["url"]).content == runtime.media
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert len(submissions(runtime)) == 1


@pytest.mark.parametrize("protocol", ["async-audio", "openai-audio"])
@pytest.mark.parametrize("case", ["defaults", "override", "explicit", "music", "zero-speed"])
def test_audio_source_namespace_priority_and_frozen_saved_defaults(audio_runtime, protocol, case):
    runtime = audio_runtime
    defaults = {
        "instructions": "保存的旁白指令",
        "voice": "saved-voice",
        "response_format": "flac",
        "speed": 2,
    }
    client, user, _, _, request = audio_seed(
        runtime,
        protocol=protocol,
        model="minimax-music-2.5" if case == "music" else "compatible-speech",
        defaults=defaults,
    )
    request["input"]["config"] = {}
    options = {}
    expected_instructions = defaults["instructions"]
    expected_voice, expected_speed = "alloy", 1
    if case in {"override", "music", "zero-speed"}:
        options = {"instructions": "本次旁白指令", "voice": "namespace-voice", "speed": 1.5}
        expected_instructions = options["instructions"]
    if case == "explicit":
        request["input"]["config"] = {
            "audioVoice": "nova",
            "audioFormat": "wav",
            "audioSpeed": "1.25",
            "audioInstructions": "  明确的旁白指令  ",
        }
        options = {"instructions": "另一份指令", "voice": "ignored-voice", "speed": 3}
        expected_voice, expected_speed, expected_instructions = "nova", 1.25, "明确的旁白指令"
    if case == "music":
        expected_voice = "namespace-voice"
    if case == "zero-speed":
        request["input"]["config"]["audioSpeed"] = "0"
        expected_speed = 1.5
    request["input"]["metadata"]["providerOptions"] = {protocol: options}
    task = admit(client, request)
    execute(runtime, task, steps=1)
    saved = client.get("/api/v1/ai-model-configs/" + request["logicalModelId"]).json()
    changed = client.patch(
        "/api/v1/ai-model-configs/" + saved["id"],
        json={
            "row_version": saved["row_version"],
            "runtime_profile": {
                **saved["runtime_profile"],
                "default_options": {"instructions": "受理后更新的指令"},
            },
        },
    )
    assert changed.status_code == 200, changed.text
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded"
    body = submissions(runtime)[0]["body"]
    assert body == {
        "model": request["model"],
        "input": request["prompt"],
        "voice": expected_voice,
        "response_format": "wav" if case == "explicit" else "mp3",
        "speed": expected_speed,
        "instructions": expected_instructions,
    }
    assert len(submissions(runtime)) == 1
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    with runtime.app[1]() as session:
        call = session.scalar(select(AIGenerationRecord))
        assert call.config_snapshot["canvas_audio_defaults"] == defaults
        assert call.request_data["canvas_request"]["input"]["metadata"]["providerOptions"] == {
            protocol: options
        }
        assert call.provider_task_id == (PROVIDER_ID if protocol == "async-audio" else None)
    assert client.get(json.loads(completed["resultJson"])["audio"]["url"]).content == runtime.media
    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]


@pytest.mark.parametrize("protocol", ["async-audio", "openai-audio"])
@pytest.mark.parametrize(
    "options",
    [
        {"instructions": True},
        {"voice": 7},
        {"speed": False},
        {"speed": 5},
        {"response_format": "private-unknown-format"},
        {"input": "private-replaced-text"},
        {"unknown": "private-unrecognised-value"},
        ["private-not-an-object"],
    ],
)
def test_audio_invalid_namespace_has_no_task_or_external_side_effect(
    audio_runtime, protocol, options
):
    runtime = audio_runtime
    client, user, _, _, request = audio_seed(runtime, protocol=protocol)
    request["input"]["metadata"]["providerOptions"] = {protocol: options}
    response = client.post(TASKS, json=request)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "generation_unsupported_parameters"
    assert "private-" not in response.text
    assert runtime.calls == []
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}


def test_audio_first_poll_is_immediate_then_uses_the_fixed_source_interval(audio_runtime):
    runtime = audio_runtime
    runtime.state["pending"] = 1
    client, _, _, _, request = audio_seed(runtime)
    task = admit(client, request)
    execute(runtime, task, steps=1)
    with runtime.app[1]() as session:
        current = session.get(AsyncTask, int(task["id"]))
        assert current.next_action == "poll"
        assert (current.next_run_at - current.updated_at).total_seconds() == 0
    execute(runtime, task, steps=1)
    with runtime.app[1]() as session:
        current = session.get(AsyncTask, int(task["id"]))
        assert current.next_action == "poll"
        assert (current.next_run_at - current.updated_at).total_seconds() == 2.5
        assert session.scalar(select(AIGenerationRecord)).provider_task_id == PROVIDER_ID
    assert len(submissions(runtime)) == 1


@pytest.mark.parametrize("protocol", ["async-audio", "openai-audio"])
@pytest.mark.parametrize("format", ["wav", "opus", "aac", "flac", "pcm"])
def test_canvas_audio_source_formats_archive_the_original_encoded_bytes(
    audio_runtime, tmp_path, protocol, format
):
    runtime = audio_runtime
    data = encoded_audio(tmp_path, format).read_bytes()
    runtime.state["media"] = data
    if format == "pcm":
        runtime.state["mime"] = "audio/pcm"
    client, _, _, path, request = audio_seed(runtime, protocol=protocol)
    request["input"]["config"]["audioFormat"] = format
    task = admit(client, request)
    _, actions = execute(runtime, task)
    assert actions == (
        ["submit", "poll", "save"] if protocol == "async-audio" else ["submit", "save"]
    )
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    result = json.loads(completed["resultJson"])["audio"]
    assert result["mimeType"] == MIMES[format] and result["bytes"] == len(data)
    if format == "pcm":
        assert result.get("durationMs") in {None, 0}
    else:
        assert 900 <= result["durationMs"] <= 1500
    assert client.get(result["url"]).content == data
    with runtime.app[1]() as session:
        file = session.scalar(select(MediaFile))
        assert file.format_code == MIMES[format]
        assert file.checksum_sha256 == hashlib.sha256(data).hexdigest()
        assert file.storage_locator.endswith("." + format)
    bound = bind(client, request, completed)
    assert bound["result"]["node"]["metadata"]["storageKey"] == result["storageKey"]
    assert (
        client.get(path + "/my-document").json()["source_document"]["nodes"][0]["metadata"][
            "mimeType"
        ]
        == MIMES[format]
    )
    assert len(submissions(runtime)) == 1
    assert submissions(runtime)[0]["body"]["response_format"] == format


@pytest.mark.parametrize("result_mode", ["content", "url", "inline", "speech"])
def test_audio_http_archive_bind_range_and_private_scope(audio_runtime, result_mode):
    runtime = audio_runtime
    runtime.state.update(poll=result_mode, pending=1 if result_mode != "speech" else 0)
    client, user, project, path, request = audio_seed(
        runtime, protocol="openai-audio" if result_mode == "speech" else "async-audio"
    )
    member, member_user = account(runtime.app, "audio_protocol_member")
    outsider, outsider_user = account(runtime.app, "audio_protocol_outsider")
    member.headers["X-Canvas-Actor"] = member_user["id"]
    outsider.headers["X-Canvas-Actor"] = outsider_user["id"]
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    executor, actions = execute(runtime, task)
    assert actions == (
        ["submit", "save"] if result_mode == "speech" else ["submit", "poll", "poll", "save"]
    )
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    media = json.loads(completed["resultJson"])["audio"]
    assert media["bytes"] == len(runtime.media) and media["mimeType"] == "audio/mpeg"
    assert 900 <= media["durationMs"] <= 1500
    assert media["storageKey"].startswith("resource:")
    assert client.get(media["url"]).content == runtime.media
    ranged = client.get(media["url"], headers={"Range": "bytes=4-15"})
    assert ranged.status_code == 206 and ranged.content == runtime.media[4:16]
    assert client.get(path + "/my-document").json() == before
    for reader in (member, outsider):
        assert reader.get(media["url"]).status_code == 404
        assert reader.get(f"{TASKS}/{task['id']}").status_code == 404
        assert reader.get(f"{TASKS}/{task['id']}/logs").status_code == 404
        assert reader.post(f"{TASKS}/{task['id']}/cancel").status_code == 404
        assert reader.post(f"{TASKS}/{task['id']}/resume").status_code == 404
    asset_path = "/api/v1/canvas-runtime/assets/" + completed["outputs"][0]["materializedAssetId"]
    assert client.get(asset_path).json()["asset"]["data"]["storageKey"] == media["storageKey"]
    assert member.get(asset_path).status_code == 404
    with runtime.app[1]() as session:
        call = session.scalar(select(AIGenerationRecord))
        assert call.adapter == ("openai_speech.v1" if result_mode == "speech" else ASYNC_AUDIO)
        assert call.provider_task_id == (None if result_mode == "speech" else PROVIDER_ID)
        file = session.scalar(select(MediaFile))
        assert file.checksum_sha256 == hashlib.sha256(runtime.media).hexdigest()
        assert (
            file.storage_locator.startswith("minio://canvas-test-")
            and "-audio/" in file.storage_locator
        )
        version = session.get(AsyncTask, int(task["id"])).message_version
        config = session.get(AIModelConfig, int(request["logicalModelId"]))
        assert config.capability_cache["adapter"] == call.adapter
    creates = submissions(runtime)
    assert len(creates) == 1 and creates[0]["authenticated"] and creates[0]["custom_header"]
    assert creates[0]["body"] == {
        "model": "compatible-speech",
        "input": request["prompt"],
        "voice": "nova",
        "response_format": "mp3",
        "speed": 1.25,
        "instructions": "温暖自然的旁白",
    }
    if result_mode == "url":
        download = next(item for item in runtime.calls if item["path"] == "/sound.mp3")
        assert not download["authenticated"] and not download["custom_header"]
    bound = bind(client, request, completed)
    assert bound["result"]["node"]["metadata"]["storageKey"] == media["storageKey"]
    assert bound["result"]["node"]["position"] == {"x": 420.25, "y": -20.5}
    assert member.get(media["url"]).status_code == 404
    assert client.get(media["url"]).content == runtime.media
    assert outsider.get(media["url"]).status_code == 404
    assert member.get(path).status_code == 404
    shared = client.get(path).json()["source_document"]["nodes"][0]["metadata"]
    assert shared["storageKey"] == media["storageKey"] and "taskId" not in shared
    assert member.get(asset_path).status_code == 404
    fresh = type(client)(runtime.app[0])
    fresh.headers["Origin"] = runtime.app[2].public_origin
    logged = fresh.post(
        "/api/v1/auth/login", json={"username": "audio_protocol_author", "password": PASSWORD}
    )
    assert logged.status_code == 200, logged.text
    fresh.headers["X-Canvas-Actor"] = user["id"]
    assert (
        fresh.get(path + "/my-document").json()["source_document"]["nodes"][0]["metadata"][
            "storageKey"
        ]
        == media["storageKey"]
    )
    executor.execute(task["id"], 1)
    executor.execute(task["id"], version)
    assert len(submissions(runtime)) == 1
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


@pytest.mark.parametrize("mode", ["lost-ack", "missing-id"])
def test_audio_uncertain_submit_never_reposts_the_original_operation(audio_runtime, mode):
    runtime = audio_runtime
    runtime.state["create"] = mode
    client, user, _, _, request = audio_seed(runtime)
    task = admit(client, request)
    executor, actions = execute(runtime, task)
    assert actions == ["submit"]
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and not failed["canRetry"] and not failed["canResume"]
    assert failed.get("resultJson") is None
    with runtime.app[1]() as session:
        assert session.scalar(select(AIGenerationRecord)).status == "unknown"
    repeated = client.post(TASKS, json=request)
    assert repeated.status_code == 200 and repeated.json()["id"] == task["id"]
    assert client.post(f"{TASKS}/{task['id']}/resume").status_code == 409
    retry = deepcopy(request)
    retry["input"]["metadata"].update(clientOperationId=uuid4().hex, retryOf=task["id"])
    assert client.post(TASKS, json=retry).status_code == 409
    executor.execute(task["id"], 1)
    assert len(submissions(runtime)) == 1
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


@pytest.mark.parametrize("error", [401, 404, 503, "malformed", "disconnect"])
def test_audio_source_poll_error_stops_without_automatic_new_submission(audio_runtime, error):
    runtime = audio_runtime
    runtime.state["poll"] = error
    client, _, _, _, request = audio_seed(runtime)
    task = admit(client, request)
    _, actions = execute(runtime, task)
    assert actions == ["submit", "poll"]
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and not failed["canRetry"]
    assert failed["providerRequestId"] == PROVIDER_ID and failed.get("resultJson") is None
    assert len(submissions(runtime)) == 1


def test_audio_poll_deadline_does_not_query_after_the_original_budget(audio_runtime):
    runtime = audio_runtime
    client, _, _, _, request = audio_seed(runtime)
    task = admit(client, request)
    execute(runtime, task, steps=1)
    with runtime.app[1].begin() as session:
        call = session.scalar(select(AIGenerationRecord))
        current = session.get(AsyncTask, int(task["id"]))
        current.started_at = utcnow() - timedelta(
            seconds=call.config_snapshot["budget_seconds"] + 1
        )
        current.created_at = current.started_at - timedelta(seconds=1)
    _, actions = execute(runtime, task)
    assert actions == ["poll"]
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and failed["errorCode"] == "generation_timeout"
    assert failed["providerRequestId"] == PROVIDER_ID and not failed["canRetry"]
    assert [item["method"] for item in runtime.calls] == ["POST"]


def test_audio_lost_poll_delivery_resumes_original_provider_identity(audio_runtime):
    runtime = audio_runtime
    client, user, _, _, request = audio_seed(runtime)
    task = admit(client, request)
    execute(runtime, task, steps=1)
    with runtime.app[1].begin() as session:
        current = session.get(AsyncTask, int(task["id"]))
        finish(current, "failed", {"code": "message_delivery_unknown"})
        # 调度投递中断的持久状态；本测试不声称经AMQP投递。
        current.next_action = "poll"
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["canResume"] and not failed["canRetry"]
    resumed = client.post(f"{TASKS}/{task['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    _, actions = execute(runtime, task)
    assert actions == ["poll", "save"]
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert len(submissions(runtime)) == 1
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


def test_audio_archive_failure_restores_original_result_without_a_second_paid_post(audio_runtime):
    runtime = audio_runtime
    runtime.state["download_failure"] = True
    client, user, _, _, request = audio_seed(runtime)
    task = admit(client, request)
    execute(runtime, task, steps=3)
    with runtime.app[1].begin() as session:
        call = session.scalar(select(AIGenerationRecord))
        original_manifest = deepcopy(call.response_data["media_manifest"])
        call.response_data = {
            **call.response_data,
            "archive_started_at": (utcnow() - timedelta(days=2)).isoformat(),
        }
    execute(runtime, task)
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and failed["canResume"] and not failed["canRetry"]
    retry = deepcopy(request)
    retry["input"]["metadata"].update(clientOperationId=uuid4().hex, retryOf=task["id"])
    assert client.post(TASKS, json=retry).status_code == 409
    runtime.state["download_failure"] = False
    resumed = client.post(f"{TASKS}/{task['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    _, actions = execute(runtime, task)
    assert actions == ["save"]
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded"
    with runtime.app[1]() as session:
        call = session.scalar(select(AIGenerationRecord))
        assert call.provider_task_id == PROVIDER_ID
        assert (
            call.response_data["media_manifest"][0]["asset_id"] == original_manifest[0]["asset_id"]
        )
    assert len(submissions(runtime)) == 1
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}


def test_audio_queued_cancel_never_reaches_the_supplier(audio_runtime):
    runtime = audio_runtime
    client, _, _, _, request = audio_seed(runtime)
    task = admit(client, request)
    cancelled = client.post(f"{TASKS}/{task['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    executor, actions = execute(runtime, task)
    assert actions == []
    executor.execute(task["id"], 1)
    assert runtime.calls == []


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_audio_provider_terminal_failure_does_not_publish_a_media_result(audio_runtime, status):
    runtime = audio_runtime
    runtime.state["poll"] = status
    client, _, _, _, request = audio_seed(runtime)
    task = admit(client, request)
    _, actions = execute(runtime, task)
    assert actions == ["submit", "poll"]
    failed = client.get(f"{TASKS}/{task['id']}").json()
    assert failed["status"] == "failed" and failed.get("resultJson") is None
    assert failed["canRetry"] and not failed["canResume"]
    assert "private-upstream-audio-message" not in json.dumps(failed)
    assert len(submissions(runtime)) == 1
