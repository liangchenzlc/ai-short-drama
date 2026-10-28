"""真实 MySQL、MinIO 和 HEVC→H.264/AAC；不调用供应商。"""

import hashlib
import json
import os
import subprocess

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import NotFound
from short_drama.domain import CanvasLocalJob, MediaFile
from short_drama.service.base import utcnow
from short_drama.service.canvas.timeline.playback.playback_cleanup import cleanup_canvas_playback
from short_drama.service.creation.assembly.rendering.renderer import executable
from short_drama.storage.models import ObjectLocation
from short_drama.tasks.canvas.canvas_local import CanvasLocalExecutor
from tests.integration.canvas.resources.test_canvas_resources import ROOT, canvas, upload
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
        or os.environ.get("RUN_CANVAS_PLAYBACK_FFMPEG") != "1",
        reason="显式启用隔离 MySQL、MinIO 与 FFmpeg 兼容播放验证",
    ),
]


def test_hevc_compatible_preview_is_deduplicated_private_and_preserves_original(
    resource_app, tmp_path
):
    app, factory, settings = resource_app
    settings = settings.model_copy(update={"render_scratch_root": str(tmp_path / "scratch")})
    app.state.settings = settings
    owner, _ = account(resource_app, "playback_owner")
    outsider, _ = account(resource_app, "playback_outsider")
    _, _, document = canvas(owner, "playback-canvas")
    source_path = tmp_path / "source-hevc.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=24:duration=1.25",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1.25",
        "-c:v",
        "libx265",
        "-x265-params",
        "pools=1:frame-threads=1:log-level=error",
        "-tag:v",
        "hvc1",
        "-c:a",
        "aac",
        "-shortest",
        source_path,
    )
    source = source_path.read_bytes()
    uploaded = upload(
        owner,
        source,
        source=document["id"],
        key="hevc-compatibility",
        kind="video",
        name="source-hevc.mp4",
        mime="video/mp4",
    )
    assert uploaded.status_code == 200, uploaded.text
    identifier = uploaded.json()["resource"]["id"]
    path = ROOT + "/" + identifier
    assert outsider.post(path + "/playback").status_code == 404
    prepared = owner.post(path + "/playback", headers={"X-Canvas-Key": document["id"]})
    assert prepared.status_code == 200, prepared.text
    task_id = prepared.json()["resource"]["playbackTaskId"]
    assert owner.post(path + "/playback").json()["resource"]["playbackTaskId"] == task_id
    assert owner.get(path + "/file?variant=playback").status_code == 409
    executor = CanvasLocalExecutor(factory, settings, app.state.storage)
    executor.execute(task_id, "1")
    ready = owner.get(path).json()["resource"]
    assert ready["playbackStatus"] == "ready", ready
    result = owner.get(path + "/file?variant=playback")
    assert result.status_code == 200 and result.headers["content-type"] == "video/mp4"
    assert result.headers["etag"].endswith(':pb"')
    target_path = tmp_path / "compatible.mp4"
    target_path.write_bytes(result.content)
    measured = subprocess.run(
        [
            executable("ffprobe"),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(target_path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    streams = json.loads(measured.stdout)["streams"]
    assert next(item for item in streams if item["codec_type"] == "video")["codec_name"] == "h264"
    assert next(item for item in streams if item["codec_type"] == "audio")["codec_name"] == "aac"
    assert abs(float(json.loads(measured.stdout)["format"]["duration"]) - 1.25) < 0.15
    ranged = owner.get(path + "/file?variant=playback", headers={"Range": "bytes=0-31"})
    assert ranged.status_code == 206 and ranged.content == result.content[:32]
    head = owner.head(path + "/file?variant=playback")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(result.content))
    assert owner.get(path + "/file").content == source
    assert outsider.get(path + "/file?variant=playback").status_code == 404
    executor.execute(task_id, "1")
    with factory() as session:
        job = session.get(CanvasLocalJob, int(task_id))
        assert job.attempts == 1 and job.status == "succeeded"
        original = session.get(MediaFile, int(identifier))
        assert original.checksum_sha256 == hashlib.sha256(source).hexdigest()
        assert (
            len(
                list(
                    session.scalars(
                        select(CanvasLocalJob).where(
                            CanvasLocalJob.source_media_id == int(identifier)
                        )
                    )
                )
            )
            == 1
        )
    assert list((tmp_path / "scratch").iterdir()) == []


def test_cancel_after_object_upload_cleans_bytes_and_explicit_resume_reuses_task(
    resource_app, tmp_path, monkeypatch
):
    app, factory, settings = resource_app
    settings = settings.model_copy(update={"render_scratch_root": str(tmp_path / "scratch")})
    app.state.settings = settings
    owner, _ = account(resource_app, "playback_cancel_owner")
    _, _, document = canvas(owner, "playback-cancel")
    source_path = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=12:duration=0.4",
        "-c:v",
        "libx264",
        source_path,
    )
    source = source_path.read_bytes()
    response = upload(
        owner,
        source,
        source=document["id"],
        key="cancel-playback-source",
        kind="video",
        name="source.mp4",
        mime="video/mp4",
    )
    identifier = response.json()["resource"]["id"]
    path = ROOT + "/" + identifier
    task = owner.post(path + "/playback").json()["resource"]["playbackTaskId"]
    storage = app.state.storage
    original_put = storage.put
    written = []

    def put_then_cancel(bucket, name, stream, size, mime):
        result = original_put(bucket, name, stream, size, mime)
        if name.startswith("canvas-playback/"):
            written.append((bucket, name))
            assert owner.post(f"/api/v1/canvas-runtime/tasks/{task}/cancel").status_code == 200
        return result

    monkeypatch.setattr(storage, "put", put_then_cancel)
    executor = CanvasLocalExecutor(factory, settings, storage)
    executor.execute(task, "1")
    assert len(written) == 1
    with pytest.raises(NotFound):
        storage.stat(*written[0])
    state = owner.get(f"/api/v1/canvas-runtime/tasks/{task}").json()
    assert state["status"] == "cancelled" and state["canResume"]
    assert owner.get(path + "/file").content == source
    monkeypatch.setattr(storage, "put", original_put)
    assert owner.post(path + "/playback").json()["resource"]["playbackTaskId"] == task
    with factory() as session:
        version = str(session.get(CanvasLocalJob, int(task)).message_version)
    executor.execute(task, version)
    assert owner.get(path).json()["resource"]["playbackStatus"] == "ready"
    assert list((tmp_path / "scratch").iterdir()) == []


def test_staging_survives_process_loss_and_cleanup_never_deletes_registered_output(
    resource_app, tmp_path
):
    from datetime import timedelta
    from io import BytesIO
    from uuid import uuid4

    app, factory, settings = resource_app
    owner, _ = account(resource_app, "playback_recovery_owner")
    _, _, document = canvas(owner, "playback-recovery")
    source_path = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=12:duration=0.4",
        "-c:v",
        "libx264",
        source_path,
    )
    response = upload(
        owner,
        source_path.read_bytes(),
        source=document["id"],
        key="recover-playback-source",
        kind="video",
        name="source.mp4",
        mime="video/mp4",
    )
    identifier = response.json()["resource"]["id"]
    path = ROOT + "/" + identifier
    task = owner.post(path + "/playback").json()["resource"]["playbackTaskId"]
    location = ObjectLocation(
        settings.minio_video_bucket, f"canvas-playback/{task}/{uuid4().hex}.mp4"
    )
    app.state.storage.put(
        location.bucket, location.object_name, BytesIO(b"interrupted"), 11, "video/mp4"
    )
    with factory.begin() as session:
        job = session.get(CanvasLocalJob, int(task))
        job.status, job.lease_token, job.locked_until = (
            "running",
            uuid4().hex,
            utcnow() + timedelta(seconds=60),
        )
        job.result_json = {"playback_staging": [location.locator]}
    assert cleanup_canvas_playback(factory, settings, app.state.storage) == 0
    with factory.begin() as session:
        session.get(CanvasLocalJob, int(task)).locked_until = utcnow() - timedelta(seconds=1)
    assert cleanup_canvas_playback(factory, settings, app.state.storage) == 1
    with pytest.raises(NotFound):
        app.state.storage.stat(location.bucket, location.object_name)
    with factory.begin() as session:
        job = session.get(CanvasLocalJob, int(task))
        job.status, job.lease_token, job.locked_until = "queued", None, None
    CanvasLocalExecutor(factory, settings, app.state.storage).execute(task, "1")
    with factory.begin() as session:
        job = session.get(CanvasLocalJob, int(task))
        output = session.get(MediaFile, job.output_media_id)
        job.result_json = {**job.result_json, "playback_staging": [output.storage_locator]}
    assert cleanup_canvas_playback(factory, settings, app.state.storage) == 1
    assert owner.get(path + "/file?variant=playback").status_code == 200
