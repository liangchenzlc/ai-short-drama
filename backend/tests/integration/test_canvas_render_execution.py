"""真实MySQL/MinIO/FFmpeg上的私人渲染、归档回执及租约恢复。"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import func, select

from short_drama.core.exceptions import StorageUnavailable
from short_drama.dao.canvas_resource_deletion_dao import CanvasResourceDeletionDAO
from short_drama.domain import CanvasRenderJob, CanvasResourceUpload, MediaFile, ProjectCanvas
from short_drama.domain.collaboration import User
from short_drama.service.base import utcnow
from short_drama.service.canvas_timeline_renderer import CanvasTimelineRenderer
from short_drama.storage.models import ObjectLocation
from short_drama.tasks.canvas_render import CanvasRenderExecutor, CanvasRenderPublisher
from tests.integration.test_canvas_resources import canvas, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_timeline_ffmpeg import ffmpeg
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_TIMELINE_FFMPEG") != "1",
        reason="Enable real isolated MySQL/MinIO/FFmpeg render verification",
    ),
]
PATH = "/api/v1/canvas-runtime"


@pytest.fixture
def runtime(resource_app, tmp_path):
    app, factory, settings = resource_app
    settings = settings.model_copy(update={"render_scratch_root": str(tmp_path / "scratch")})
    app.state.settings = settings
    owner, user = account(resource_app, "canvas_render_executor")
    project, _, document = canvas(owner, "canvas-render-executor-project")
    video = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x180:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=220:duration=1",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        video,
    )
    content = video.read_bytes()
    response = upload(
        owner,
        content,
        key="render-source",
        source=document["id"],
        kind="video",
        name="source.mp4",
        mime="video/mp4",
    )
    assert response.status_code == 200, response.text
    source = response.json()["resource"]
    body = {
        "projectId": document["id"],
        "clientOperationId": "canvas-render-executor-operation",
        "timeline": {
            "version": 2,
            "durationMs": 1000,
            "tracks": [{"id": "v"}, {"id": "s"}],
            "clips": [
                {
                    "id": "video",
                    "kind": "video",
                    "trackId": "v",
                    "durationMs": 1000,
                    "directMedia": {"storageKey": "resource:" + source["id"]},
                },
                {
                    "id": "subtitle",
                    "kind": "subtitle",
                    "trackId": "s",
                    "durationMs": 1000,
                    "text": "持久渲染字幕",
                },
            ],
        },
        "options": {"width": 320, "height": 180},
    }
    response = owner.post(PATH + "/timeline/renders", json=body)
    assert response.status_code == 202, response.text
    executor = CanvasRenderExecutor(factory, settings, app.state.storage)
    return owner, user, project, source, response.json(), executor, resource_app


def detail(runtime):
    owner, _, _, _, task, _, _ = runtime
    response = owner.get(PATH + f"/tasks/{task['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def output_upload(runtime):
    _, user, _, _, task, _, app = runtime
    with app[1]() as session:
        return session.scalar(
            select(CanvasResourceUpload).where(
                CanvasResourceUpload.user_id == int(user["id"]),
                CanvasResourceUpload.idempotency_hash
                == hashlib.sha256(f"canvas-render:{task['id']}:0".encode()).hexdigest(),
            )
        )


def execute(runtime, version=1):
    runtime[5].execute(runtime[4]["id"], str(version))


def resume(runtime):
    response = runtime[0].post(PATH + f"/tasks/{runtime[4]['id']}/resume")
    assert response.status_code == 200 and response.json()["status"] == "queued", response.text


def forbid_render(*_args, **_kwargs):
    raise AssertionError("A recovered/duplicate/stale task must not encode again")


def test_real_render_archive_is_private_and_duplicate_messages_do_not_encode(runtime, monkeypatch):
    execute(runtime)
    state = detail(runtime)
    assert state["status"] == "succeeded" and state["progress"] == 100, state
    result = json.loads(state["resultJson"])
    assert result["fileName"] == "timeline-render.mp4"
    assert "持久渲染字幕" in result["subtitleSrt"] and result["durationMs"] >= 1000
    owner, _, project, source, task, _, app = runtime
    member, member_user = account(app, "canvas_render_member")
    outsider, _ = account(app, "canvas_render_outsider")
    join(app, owner, member, project["id"], member_user["id"])
    endpoint = PATH + f"/resources/{result['resourceId']}/file"
    downloaded = owner.get(endpoint)
    assert downloaded.status_code == 200 and len(downloaded.content) == result["size"]
    assert member.get(endpoint).status_code == 404 and outsider.get(endpoint).status_code == 404
    assert member.get(PATH + f"/tasks/{task['id']}").status_code == 404
    with app[1]() as session:
        job = session.get(CanvasRenderJob, int(task["id"]))
        media = session.get(MediaFile, job.output_media_id)
        assert str(media.id) == result["resourceId"] and str(media.id) != source["id"]
        assert media.published_at is None and (media.width, media.height) == (320, 180)
        assert media.checksum_sha256 == hashlib.sha256(downloaded.content).hexdigest()
        assert job.attempts == 1 and job.lease_token is None and job.locked_until is None
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 2
        references = CanvasResourceDeletionDAO(session)
        assert references.referenced(session.get(MediaFile, int(source["id"])))
        assert references.referenced(media)
    monkeypatch.setattr(CanvasTimelineRenderer, "render", forbid_render)
    execute(runtime)
    execute(runtime, 0)
    assert detail(runtime) == state
    assert output_upload(runtime).status == "ready"
    assert not tuple(Path(runtime[5].settings.render_scratch_root).iterdir())


def test_execution_uses_frozen_locator_after_current_media_metadata_changes(runtime):
    with runtime[6][1].begin() as session:
        source = session.get(MediaFile, int(runtime[3]["id"]))
        original_locator = source.storage_locator
        source.storage_locator = original_locator + "-unavailable-new-location"
    execute(runtime)
    assert detail(runtime)["status"] == "succeeded", detail(runtime)
    with runtime[6][1]() as session:
        frozen = session.get(CanvasRenderJob, int(runtime[4]["id"])).source_snapshot
        assert frozen[runtime[3]["id"]]["locator"] == original_locator


@pytest.mark.parametrize("tamper", ["bytes", "metadata"])
def test_recovery_verifies_archived_bytes_and_actual_media_before_success(
    runtime, monkeypatch, tamper
):
    executor = runtime[5]
    original = executor._finish

    def lose_receipt(*_args):
        raise StorageUnavailable("Injected lost completion acknowledgement")

    monkeypatch.setattr(executor, "_finish", lose_receipt)
    execute(runtime)
    assert detail(runtime)["status"] == "failed"
    upload = output_upload(runtime)
    assert upload.status == "ready"
    app = runtime[6]
    if tamper == "bytes":
        location = ObjectLocation.parse(upload.storage_locator, {app[2].minio_video_bucket})
        with app[0].state.storage.open(location.bucket, location.object_name) as stream:
            content = stream.read()
        altered = bytes([content[0] ^ 1]) + content[1:]
        app[0].state.storage.put(
            location.bucket, location.object_name, BytesIO(altered), len(altered), "video/mp4"
        )
    else:
        with app[1].begin() as session:
            session.get(MediaFile, upload.media_id).width = 640
    monkeypatch.setattr(executor, "_finish", original)
    monkeypatch.setattr(CanvasTimelineRenderer, "render", forbid_render)
    resume(runtime)
    execute(runtime, 2)
    state = detail(runtime)
    assert state["status"] == "failed" and "校验" in state["error"], state
    assert "resultJson" not in state and output_upload(runtime).media_id == upload.media_id


def test_permission_revoked_after_archive_cannot_publish_completion(runtime, monkeypatch):
    executor, app = runtime[5:]
    original = executor._finish

    def revoke_then_finish(*args):
        with app[1].begin() as session:
            session.get(User, int(runtime[1]["id"])).status = "disabled"
        return original(*args)

    monkeypatch.setattr(executor, "_finish", revoke_then_finish)
    execute(runtime)
    with app[1]() as session:
        job = session.get(CanvasRenderJob, int(runtime[4]["id"]))
        assert job.status == "cancelled" and job.result_json is None and job.output_media_id is None
        upload = session.get(CanvasResourceUpload, output_upload(runtime).id)
        assert upload.status == "ready"
        assert session.get(MediaFile, upload.media_id).published_at is None


def test_lost_ack_after_committed_completion_keeps_success_and_original_output(
    runtime, monkeypatch
):
    executor = runtime[5]
    original = executor._finish

    def finish_then_lose_ack(*args):
        original(*args)
        raise StorageUnavailable("Injected lost committed completion acknowledgement")

    monkeypatch.setattr(executor, "_finish", finish_then_lose_ack)
    execute(runtime)
    state = detail(runtime)
    assert state["status"] == "succeeded", state
    monkeypatch.setattr(CanvasTimelineRenderer, "render", forbid_render)
    execute(runtime)
    assert detail(runtime) == state
    assert output_upload(runtime).media_id == int(json.loads(state["resultJson"])["resourceId"])


def test_lost_chunk_ack_reencodes_and_reuses_the_same_reserved_artifact(runtime, monkeypatch):
    storage = runtime[6][0].state.storage
    original_put, original_render = storage.put, CanvasTimelineRenderer.render
    failed, render_calls = False, []

    def lose_chunk_ack(bucket, key, *args, **kwargs):
        nonlocal failed
        result = original_put(bucket, key, *args, **kwargs)
        if key.startswith("canvas/uploads/") and not failed:
            failed = True
            raise StorageUnavailable("Injected lost chunk acknowledgement")
        return result

    def count_render(renderer, *args, **kwargs):
        render_calls.append("render")
        return original_render(renderer, *args, **kwargs)

    monkeypatch.setattr(storage, "put", lose_chunk_ack)
    monkeypatch.setattr(CanvasTimelineRenderer, "render", count_render)
    execute(runtime)
    assert detail(runtime)["status"] == "failed"
    pending = output_upload(runtime)
    assert pending is not None and pending.status == "pending"
    resume(runtime)
    execute(runtime, 2)
    state = detail(runtime)
    assert state["status"] == "succeeded", state
    assert len(render_calls) == 2
    assert json.loads(state["resultJson"])["resourceId"] == str(pending.reserved_resource_id)
    assert output_upload(runtime).id == pending.id


def test_publish_failure_retains_original_job_and_retries_same_message_version(
    runtime, monkeypatch
):
    publisher = CanvasRenderPublisher(runtime[6][1], runtime[5].settings)
    delivered = []

    def fail_broker(*_args, **_kwargs):
        raise ConnectionError("Injected broker publication failure")

    monkeypatch.setattr(publisher.app, "send_task", fail_broker)
    with pytest.raises(RuntimeError, match="发布画布渲染任务失败"):
        publisher.tick()
    assert detail(runtime)["status"] == "queued" and not publisher.tick()
    monkeypatch.setattr(
        publisher.app, "send_task", lambda *args, **kwargs: delivered.append(kwargs)
    )
    with runtime[6][1].begin() as session:
        session.get(CanvasRenderJob, int(runtime[4]["id"])).next_run_at = utcnow() - timedelta(
            seconds=1
        )
    assert publisher.tick()
    assert delivered[0]["args"] == [runtime[4]["id"], "1"]
    assert delivered[0]["routing_key"] == "render" and delivered[0]["retry"] is False


@pytest.mark.parametrize("cancelled", [False, True])
def test_recovery_of_exhausted_or_cancelled_expired_lease_is_terminal(
    runtime, monkeypatch, cancelled
):
    publisher = CanvasRenderPublisher(runtime[6][1], runtime[5].settings)
    monkeypatch.setattr(publisher.app, "send_task", forbid_render)
    with runtime[6][1].begin() as session:
        job = session.get(CanvasRenderJob, int(runtime[4]["id"]))
        job.status, job.lease_token = "running", "a" * 32
        job.locked_until = utcnow() - timedelta(seconds=1)
        job.attempts, job.cancel_requested = 3, int(cancelled)
    assert not publisher.tick()
    assert detail(runtime)["status"] == ("cancelled" if cancelled else "failed")


@pytest.mark.parametrize("corruption", ["checksum", "size"])
def test_source_reads_check_the_frozen_bytes_before_encoding(runtime, corruption):
    owner, _, _, source, task, executor, app = runtime
    with app[1]() as session:
        frozen = session.get(CanvasRenderJob, int(task["id"])).source_snapshot[source["id"]]
    location = ObjectLocation.parse(frozen["locator"], {app[2].minio_video_bucket})
    data = owner.get(PATH + f"/resources/{source['id']}/file").content
    changed = (bytes([data[0] ^ 1]) + data[1:]) if corruption == "checksum" else data + b"x"
    app[0].state.storage.put(
        location.bucket, location.object_name, BytesIO(changed), len(changed), "video/mp4"
    )
    executor.execute(task["id"], "1")
    state = detail(runtime)
    assert state["status"] == "failed" and "校验" in state["error"], state
    assert output_upload(runtime) is None


def test_cancelled_and_old_message_versions_never_start_encoding(runtime, monkeypatch):
    monkeypatch.setattr(CanvasTimelineRenderer, "render", forbid_render)
    owner, _, _, _, task, _, _ = runtime
    assert owner.post(PATH + f"/tasks/{task['id']}/cancel").json()["status"] == "cancelled"
    execute(runtime)
    resume(runtime)
    execute(runtime)
    assert detail(runtime)["status"] == "queued" and output_upload(runtime) is None


def test_running_cancel_stops_before_archive_and_does_not_replace_terminal_state(
    runtime, monkeypatch
):
    original = CanvasTimelineRenderer.render
    owner, _, _, _, task, _, _ = runtime

    def cancel_then_render(renderer, *args, **kwargs):
        cancelled = owner.post(PATH + f"/tasks/{task['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "running"
        return original(renderer, *args, **kwargs)

    monkeypatch.setattr(CanvasTimelineRenderer, "render", cancel_then_render)
    execute(runtime)
    assert detail(runtime)["status"] == "cancelled" and output_upload(runtime) is None


@pytest.mark.parametrize("boundary", ["storage_ack", "completion_ack"])
def test_complete_archive_recovery_reuses_original_bytes_and_one_resource(
    runtime, monkeypatch, boundary
):
    executor = runtime[5]
    if boundary == "storage_ack":
        storage = runtime[6][0].state.storage
        original = storage.put
        failed = False

        def lose_ack(bucket, key, *args, **kwargs):
            nonlocal failed
            result = original(bucket, key, *args, **kwargs)
            if key.startswith("canvas/resources/") and not failed:
                failed = True
                raise StorageUnavailable("Injected lost output storage acknowledgement")
            return result

        monkeypatch.setattr(storage, "put", lose_ack)
    else:
        original = executor._finish

        def lose_finish(*_args):
            raise StorageUnavailable("Injected lost completion acknowledgement")

        monkeypatch.setattr(executor, "_finish", lose_finish)
    execute(runtime)
    state = detail(runtime)
    assert state["status"] == "failed", state
    upload = output_upload(runtime)
    assert upload is not None and upload.status == (
        "pending" if boundary == "storage_ack" else "ready"
    )
    reserved = upload.reserved_resource_id
    if boundary == "completion_ack":
        monkeypatch.setattr(executor, "_finish", original)
    monkeypatch.setattr(CanvasTimelineRenderer, "render", forbid_render)
    resume(runtime)
    execute(runtime, 2)
    state = detail(runtime)
    assert state["status"] == "succeeded", state
    assert json.loads(state["resultJson"])["resourceId"] == str(reserved)
    assert output_upload(runtime).status == "ready"
    with runtime[6][1]() as session:
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 2


def test_two_deliveries_only_one_lease_encodes_and_archives(runtime, monkeypatch):
    entered, released = Event(), Event()
    original = CanvasTimelineRenderer.render

    def hold(renderer, *args, **kwargs):
        entered.set()
        assert released.wait(20), "Second delivery did not finish"
        return original(renderer, *args, **kwargs)

    monkeypatch.setattr(CanvasTimelineRenderer, "render", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute, runtime)
        try:
            assert entered.wait(10)
            pool.submit(execute, runtime).result(timeout=10)
        finally:
            released.set()
        first.result(timeout=30)
    assert detail(runtime)["status"] == "succeeded"
    with runtime[6][1]() as session:
        assert session.get(CanvasRenderJob, int(runtime[4]["id"])).attempts == 1


def test_expired_lease_requeues_with_new_version_and_fences_old_worker(runtime, monkeypatch):
    executor, app = runtime[5:]
    publisher = CanvasRenderPublisher(app[1], executor.settings)
    delivered = []
    monkeypatch.setattr(
        publisher.app, "send_task", lambda *args, **kwargs: delivered.append((args, kwargs))
    )
    original = CanvasTimelineRenderer.render

    def expire_then_render(renderer, *args, **kwargs):
        with app[1].begin() as session:
            job = session.get(CanvasRenderJob, int(runtime[4]["id"]))
            job.locked_until = utcnow() - timedelta(seconds=1)
        assert publisher.tick()
        return original(renderer, *args, **kwargs)

    monkeypatch.setattr(CanvasTimelineRenderer, "render", expire_then_render)
    execute(runtime)
    assert detail(runtime)["status"] == "queued", detail(runtime)
    assert delivered[0][1]["args"] == [runtime[4]["id"], "2"]
    monkeypatch.setattr(CanvasTimelineRenderer, "render", original)
    execute(runtime, 2)
    assert detail(runtime)["status"] == "succeeded"


@pytest.mark.parametrize("permission", ["disabled_user", "archived_canvas"])
def test_revoked_permission_cancels_without_reading_or_encoding(runtime, monkeypatch, permission):
    monkeypatch.setattr(CanvasTimelineRenderer, "render", forbid_render)
    _, user, project, _, task, _, app = runtime
    with app[1].begin() as session:
        if permission == "disabled_user":
            session.get(User, int(user["id"])).status = "disabled"
        else:
            session.get(ProjectCanvas, int(project["primary_canvas_id"])).archived_at = utcnow()
    execute(runtime)
    with app[1]() as session:
        job = session.get(CanvasRenderJob, int(task["id"]))
        assert job.status == "cancelled" and job.output_media_id is None and job.attempts == 0


def test_failure_receipt_cannot_be_silently_acknowledged_when_database_write_fails(
    runtime, monkeypatch
):
    def fail_encoding(*_args, **_kwargs):
        raise RuntimeError("时间线编码器不可用")

    def fail_receipt(*_args):
        raise RuntimeError("Injected durable receipt outage")

    monkeypatch.setattr(CanvasTimelineRenderer, "render", fail_encoding)
    monkeypatch.setattr(runtime[5], "_fail", fail_receipt)
    with pytest.raises(RuntimeError, match="durable receipt outage"):
        execute(runtime)
    assert detail(runtime)["status"] == "running"
