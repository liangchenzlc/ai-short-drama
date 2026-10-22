"""原取帧、剪辑、裁切、音视频分离的真实浏览器与永久媒体验证。"""

import hashlib
import json
import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.domain import AsyncTask, MediaFile
from short_drama.service.canvas_timeline_renderer import CanvasTimelineRenderer
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.resources.test_canvas_resources import ROOT, canvas, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.timeline.test_canvas_timeline_ffmpeg import ffmpeg
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_TIMELINE_FFMPEG") != "1",
        reason="Enable original media browser/FFmpeg/HTTP/MySQL/MinIO verification",
    ),
]


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize("kind", ["frames", "trim", "crop", "audio"])
def test_original_media_tools_keep_source_outputs_links_and_cookie_only_recovery(
    resource_app, tmp_path, width, kind
):
    node = shutil.which("node")
    assert node
    owner, _ = account(resource_app, "media_tools_owner")
    member, member_user = account(resource_app, "media_tools_member")
    outsider, _ = account(resource_app, "media_tools_outsider")
    project, path, document = canvas(owner, "media-tools-project")
    video = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x180:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x180:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=220:duration=2",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        video,
    )
    response = upload(
        owner,
        video.read_bytes(),
        key="media-tools-source",
        source=document["id"],
        kind="video",
        name="source.mp4",
        mime="video/mp4",
    )
    assert response.status_code == 200, response.text
    media = response.json()["resource"]
    document["nodes"] = [
        {
            "id": "media-video",
            "type": "video",
            "title": "媒体工具来源",
            "position": {"x": -200, "y": -120},
            "width": 400,
            "height": 225,
            "metadata": {
                "status": "success",
                "content": f"{ROOT}/{media['id']}/file",
                "storageKey": "resource:" + media["id"],
                "naturalWidth": 320,
                "naturalHeight": 180,
                "durationMs": 2000,
                "hasAudio": True,
                "mimeType": "video/mp4",
            },
        }
    ]
    committed = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "media-tools-fixture"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert committed.status_code == 200, committed.text
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    output_directory = tmp_path / "output" / "playwright"
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "media_tools_owner",
            "memberUsername": "media_tools_member",
            "password": PASSWORD,
            "width": width,
            "kind": kind,
            "outputDirectory": str(output_directory),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-media-tools-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=300)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Original media tools browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
    assert process.returncode == 0, f"Original media tools browser failed: {errors}\n{output}"
    result = json.loads(output)
    assert result["step"] == "complete" and result["cookie_only_recovery"]
    assert not result["page_errors"] and not result["http_errors"] and result["task_posts"] == 0
    saved = owner.get(path + "/my-document").json()["source_document"]
    source = next(item for item in saved["nodes"] if item["id"] == "media-video")
    assert source["metadata"]["storageKey"] == "resource:" + media["id"]
    assert owner.get(f"{ROOT}/{media['id']}/file").content == video.read_bytes()
    renderer = CanvasTimelineRenderer(settings)
    with resource_app[1]() as session:
        assert list(session.scalars(select(AsyncTask))) == []
        for item in result["outputs"]:
            derived = next(value for value in saved["nodes"] if value["id"] == item["id"])
            assert any(
                edge["fromNodeId"] == "media-video" and edge["toNodeId"] == item["id"]
                for edge in saved["connections"]
            )
            endpoint = f"{ROOT}/{item['resource_id']}/file"
            downloaded = owner.get(endpoint)
            assert downloaded.status_code == 200
            assert member.get(endpoint).status_code == 404
            assert outsider.get(endpoint).status_code == 404
            assert hashlib.sha256(downloaded.content).hexdigest() == item["checksum"]
            stored = session.get(MediaFile, int(item["resource_id"]))
            assert stored.published_at is not None and stored.checksum_sha256 == item["checksum"]
            artifact = output_directory / item["filename"]
            assert artifact.read_bytes() == downloaded.content
            if kind == "frames":
                image = Image.open(BytesIO(downloaded.content)).convert("RGB")
                assert image.size == (320, 180)
                pixel = image.getpixel((160, 90))
                time_ms = derived["metadata"]["videoFrameTimeMs"]
                # 固定源 normalizeVideoFrameTimes 保留末尾 100ms，避免尾帧解码失败。
                assert time_ms in (0, 1900)
                assert pixel[0 if time_ms == 0 else 2] >= 245, pixel
                assert pixel[2 if time_ms == 0 else 0] <= 10, pixel
            else:
                probe = renderer.probe(artifact)
                if kind == "trim":
                    metadata = derived["metadata"]
                    assert metadata["videoTrimSource"]["storageKey"] == "resource:" + media["id"]
                    assert metadata["videoTrimStartMs"] == 0
                    assert 850 <= metadata["videoTrimEndMs"] <= 1150
                    assert abs(probe.facts.duration_ms - metadata["videoTrimEndMs"]) < 100
                    assert probe.facts.has_video and probe.facts.has_audio
                    assert (probe.width, probe.height) == (320, 180)
                elif kind == "crop":
                    assert derived["metadata"]["videoCrop"] == {
                        "x": 32,
                        "y": 18,
                        "width": 256,
                        "height": 144,
                    }
                    assert (probe.width, probe.height) == (256, 144)
                    assert probe.facts.has_video and probe.facts.has_audio
                    assert 1950 <= probe.facts.duration_ms < 2200
                else:
                    assert kind == "audio"
                    assert probe.facts.has_video == (item["kind"] == "video")
                    assert probe.facts.has_audio == (item["kind"] == "audio")
                    assert 1950 <= probe.facts.duration_ms < 2200
                    if item["kind"] == "video":
                        assert (probe.width, probe.height) == (320, 180)
