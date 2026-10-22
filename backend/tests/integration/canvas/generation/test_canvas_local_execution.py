"""真实 MySQL/MinIO/FFmpeg 与受控 whisper HTTP；不运行 ASR 模型或 broker。"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from short_drama.domain import AsyncTask, CanvasLocalJob, CanvasResourceUpload, MediaFile
from short_drama.domain.collaboration import User
from short_drama.service.base import utcnow
from short_drama.tasks.canvas_local import CanvasLocalExecutor, CanvasLocalPublisher
from tests.integration.canvas.audio.test_canvas_transcription_wav_http import whisper_server
from tests.integration.canvas.resources.test_canvas_resources import canvas, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.timeline.test_canvas_timeline_ffmpeg import ffmpeg
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_TRANSCRIPTION_FFMPEG") != "1",
        reason="Enable real isolated MySQL/MinIO/FFmpeg transcription checks",
    ),
]
PATH = "/api/v1/canvas-runtime"


@pytest.fixture
def runtime(resource_app, tmp_path):
    app, factory, settings = resource_app
    settings = settings.model_copy(update={"render_scratch_root": str(tmp_path / "scratch")})
    app.state.settings = settings
    owner, user = account(resource_app, "canvas_local_executor")
    project, _, document = canvas(owner, "canvas-local-executor-project")
    path = tmp_path / "source.wav"
    ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.4", "-ac", "2", path
    )
    content = path.read_bytes()
    response = upload(
        owner,
        content,
        key="local-source",
        source=document["id"],
        kind="audio",
        name="source.wav",
        mime="audio/wav",
    )
    assert response.status_code == 200, response.text
    source = response.json()["resource"]
    body = {
        "projectId": document["id"],
        "resourceId": source["id"],
        "language": "zh",
        "clientOperationId": "canvas-local-executor-operation",
    }
    response = owner.post(PATH + "/timeline/transcriptions", json=body)
    assert response.status_code == 202, response.text
    executor = CanvasLocalExecutor(factory, settings, app.state.storage)
    return owner, user, project, source, response.json(), executor, resource_app, content


def detail(runtime):
    response = runtime[0].get(PATH + f"/tasks/{runtime[4]['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def execute(runtime, version=1):
    runtime[5].execute(runtime[4]["id"], str(version))


def resume(runtime):
    response = runtime[0].post(PATH + f"/tasks/{runtime[4]['id']}/resume")
    assert response.status_code == 200 and response.json()["status"] == "queued", response.text


def assert_clean(runtime):
    from pathlib import Path

    assert list(Path(runtime[5].settings.render_scratch_root).glob("beeftv-whisper-*")) == []


def test_real_transcription_is_private_and_duplicates_do_not_submit(runtime):
    with whisper_server() as (url, received):
        runtime[5].settings.canvas_whisper_base_url = url
        execute(runtime)
        state = detail(runtime)
        assert state["status"] == "succeeded" and state["progress"] == 100, state
        result = json.loads(state["resultJson"])
        assert result["segments"] == [{"startMs": 1, "endMs": 350, "text": "中文语音"}]
        assert result["language"] == "zh" and result["srt"].endswith("中文语音\n\n")
        execute(runtime)
        assert len(received) == 1 and detail(runtime) == state
        assert runtime[0].post(PATH + f"/tasks/{state['id']}/resume").status_code == 409
    owner, user, project, source, task, _, app, content = runtime
    assert owner.get(PATH + f"/resources/{source['id']}/file").content == content
    fresh = TestClient(
        app[0], base_url=app[2].public_origin, headers={"Origin": app[2].public_origin}
    )
    try:
        fresh.cookies.update(owner.cookies)
        assert fresh.get(PATH + f"/tasks/{task['id']}").json() == state
    finally:
        fresh.close()
    member, member_user = account(app, "local_executor_member")
    outsider, _ = account(app, "local_executor_outsider")
    for client in (member, outsider):
        for suffix in ("", "/logs"):
            assert client.get(PATH + f"/tasks/{task['id']}" + suffix).status_code == 404
    with app[1]() as session:
        job = session.get(CanvasLocalJob, int(task["id"]))
        assert job.user_id == int(user["id"]) and job.result_json == result
        assert job.lease_token is None and job.locked_until is None and job.attempts == 1
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 1
    assert_clean(runtime)


def test_failed_http_is_terminal_until_explicit_same_task_resume(runtime):
    with whisper_server(status=503, payload=b"controlled whisper unavailable") as (url, failed):
        runtime[5].settings.canvas_whisper_base_url = url
        execute(runtime)
        state = detail(runtime)
        assert state["status"] == "failed" and "返回 503" in state["error"]
        assert state["canResume"] and "resultJson" not in state
        execute(runtime)
        assert len(failed) == 1
    resume(runtime)
    with whisper_server() as (url, succeeded):
        runtime[5].settings.canvas_whisper_base_url = url
        execute(runtime, version=1)
        assert succeeded == []
        execute(runtime, version=2)
        assert len(succeeded) == 1 and detail(runtime)["status"] == "succeeded"
    with runtime[6][1]() as session:
        job = session.get(CanvasLocalJob, int(runtime[4]["id"]))
        assert job.attempts == 2 and job.message_version == 2
    assert_clean(runtime)


def test_missing_service_configuration_records_failure_and_cleans(runtime):
    runtime[5].settings.canvas_whisper_base_url = ""
    execute(runtime)
    state = detail(runtime)
    assert state["status"] == "failed" and "CANVAS_WHISPER_BASE_URL" in state["error"]
    assert state["errorCode"] == "canvas_transcription_failed"
    assert_clean(runtime)


def test_global_private_transcription_executes_with_real_wav_and_http(runtime):
    owner, user, _, source, _, executor, app, _ = runtime
    with app[1].begin() as session:
        media = session.get(MediaFile, int(source["id"]))
        media.project_id, media.scope_user_id = None, int(user["id"])
        upload = session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.media_id == media.id)
        )
        upload.project_id = upload.canvas_id = None
        upload.scope_user_id = int(user["id"])
    response = owner.post(
        PATH + "/timeline/transcriptions",
        json={
            "resourceId": source["id"],
            "clientOperationId": "global-transcription-execution",
            "language": "zh",
        },
    )
    assert response.status_code == 202, response.text
    task = response.json()
    with whisper_server() as (url, received):
        executor.settings.canvas_whisper_base_url = url
        executor.execute(task["id"], "1")
        assert len(received) == 1
    state = owner.get(PATH + f"/tasks/{task['id']}").json()
    assert state["status"] == "succeeded" and "projectId" not in state, state
    assert json.loads(state["resultJson"])["segments"][0]["text"] == "中文语音"
    assert_clean(runtime)


def test_source_checksum_mismatch_stops_before_http(runtime):
    with runtime[6][1].begin() as session:
        job = session.get(CanvasLocalJob, int(runtime[4]["id"]))
        job.source_snapshot = {**job.source_snapshot, "checksum": "b" * 64}
    with whisper_server() as (url, received):
        runtime[5].settings.canvas_whisper_base_url = url
        execute(runtime)
        assert received == []
    state = detail(runtime)
    assert state["status"] == "failed" and "字节校验" in state["error"]
    assert_clean(runtime)


def test_source_metadata_changes_do_not_replace_frozen_media_locator(runtime):
    with runtime[6][1].begin() as session:
        source = session.get(MediaFile, int(runtime[3]["id"]))
        source.storage_locator = source.storage_locator + ".changed"
    with whisper_server() as (url, received):
        runtime[5].settings.canvas_whisper_base_url = url
        execute(runtime)
        assert len(received) == 1 and detail(runtime)["status"] == "succeeded"
    assert_clean(runtime)


def test_queued_cancel_does_not_open_or_transcribe_and_resume_keeps_id(runtime):
    task = runtime[4]
    assert runtime[0].post(PATH + f"/tasks/{task['id']}/cancel").json()["status"] == "cancelled"
    with whisper_server() as (url, received):
        runtime[5].settings.canvas_whisper_base_url = url
        execute(runtime)
        assert received == []
        resume(runtime)
        execute(runtime, 2)
        assert len(received) == 1 and detail(runtime)["id"] == task["id"]
    assert_clean(runtime)


@pytest.mark.parametrize("stop", ["cancel", "disable_user"])
def test_running_http_stops_on_cancellation_or_actor_revocation(runtime, stop):
    with whisper_server(wait_for_release=True) as (url, received):
        runtime[5].settings.canvas_whisper_base_url = url
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execute, runtime)
            deadline = time.monotonic() + 5
            while not received and not future.done() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert len(received) == 1
            if stop == "cancel":
                assert (
                    runtime[0].post(PATH + f"/tasks/{runtime[4]['id']}/cancel").status_code == 200
                )
            else:
                with runtime[6][1].begin() as session:
                    session.get(User, int(runtime[1]["id"])).status = "disabled"
            future.result(timeout=5)
        assert len(received) == 1
    with runtime[6][1]() as session:
        job = session.get(CanvasLocalJob, int(runtime[4]["id"]))
        assert job.status == "cancelled" and job.result_json is None
        assert job.lease_token is None and job.locked_until is None
    assert_clean(runtime)


def test_expired_lease_requeues_same_job_and_stale_message_cannot_claim(runtime, monkeypatch):
    with runtime[6][1].begin() as session:
        job = session.get(CanvasLocalJob, int(runtime[4]["id"]))
        original = deepcopy(job.source_snapshot)
        job.status, job.lease_token, job.locked_until, job.attempts = (
            "running",
            "a" * 32,
            utcnow() - timedelta(seconds=1),
            1,
        )
    publisher = CanvasLocalPublisher(runtime[6][1], runtime[5].settings)
    sent = []
    monkeypatch.setattr(
        publisher.app, "send_task", lambda *args, **kwargs: sent.append((args, kwargs))
    )
    assert publisher.tick() and len(sent) == 1
    assert sent[0][0] == ("short_drama.execute_canvas_local",)
    assert sent[0][1]["args"] == [runtime[4]["id"], "2"]
    with whisper_server() as (url, received):
        runtime[5].settings.canvas_whisper_base_url = url
        execute(runtime, 1)
        assert received == []
        execute(runtime, 2)
        assert len(received) == 1
    with runtime[6][1]() as session:
        job = session.get(CanvasLocalJob, int(runtime[4]["id"]))
        assert job.source_snapshot == original and job.status == "succeeded"
    assert_clean(runtime)
