"""原音视频离线稿通过显式保存恢复；真实MySQL/MinIO，故障受控且不调用模型。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import wave
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.domain import CanvasResourceUpload, MediaFile
from short_drama.service.video_render import executable
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.resources.test_canvas_resources import ROOT, canvas
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable isolated original pending audio/video browser acceptance",
    ),
]


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("scenario", ["upload-unavailable", "lost-upload-receipt"])
@pytest.mark.parametrize("kind", ["audio", "video"])
@pytest.mark.parametrize("failure_timing", ["confirmed-placeholder", "immediate"])
def test_original_browser_recovers_pending_media(
    resource_app, tmp_path, width, scenario, kind, failure_timing
):
    owner, owner_user = account(resource_app, "pending_media_owner")
    member, member_user = account(resource_app, "pending_media_member")
    outsider, _ = account(resource_app, "pending_media_outsider")
    project, path, document = canvas(owner, "pending-media-browser")
    if kind == "audio":
        sound = BytesIO()
        with wave.open(sound, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(32000))
        body, name, mime = sound.getvalue(), "原音频.wav", "audio/wav"
    else:
        video = tmp_path / "original.mp4"
        subprocess.run(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=blue:size=160x90:rate=24",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(video),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        body, name, mime = video.read_bytes(), "原视频.mp4", "video/mp4"
    config = {
        "username": "pending_media_owner",
        "password": PASSWORD,
        "userId": owner_user["id"],
        "otherUserId": member_user["id"],
        "sourceKey": document["id"],
        "path": path,
        "width": width,
        "scenario": scenario,
        "kind": kind,
        "immediateFailure": failure_timing == "immediate",
        "outputDirectory": str(tmp_path / "browser"),
        "media": {"name": name, "mimeType": mime, "content": base64.b64encode(body).decode()},
    }
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4196"})
    node = shutil.which("node")
    assert node, "Node.js is required for original pending media browser verification"
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [node, "scripts/verify-pending-media-python.mjs"],
            cwd=Path(__file__).resolve().parents[5] / "frontend" / "canvas",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            stdout, errors = process.communicate(json.dumps(config), timeout=420)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            stdout, errors = process.communicate(timeout=10)
            pytest.fail(f"Pending media browser timed out: {errors}\n{stdout}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Pending media browser failed: {errors}\n{stdout}"
    report = json.loads(stdout)
    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["failure_timing"] == failure_timing
    assert report["pending_media_original_bytes_after_reload"]
    assert report["explicit_save_reused_upload_key"]
    assert report["cookies_only_recovery_without_reupload"] and report["task_posts"] == 0
    if scenario == "lost-upload-receipt":
        assert report["explicit_save_reused_ready_identity"]
    saved = owner.get(path + "/my-document").json()["source_document"]
    assert member.get(path + "/my-document").status_code == 404
    shared = owner.get(path).json()["source_document"]
    assert len(saved["nodes"]) == len(shared["nodes"]) == 1
    item, member_node = saved["nodes"][0], shared["nodes"][0]
    identity = item["metadata"]["storageKey"].removeprefix("resource:")
    assert identity.isdecimal() and int(identity) > 2**53
    assert item["metadata"]["assetId"] == report["resource"]["asset_id"]
    assert item["id"] == member_node["id"] == report["resource"]["node_id"]
    assert item["type"] == member_node["type"] == kind
    assert item["metadata"]["status"] == "success"
    assert item["metadata"]["durationMs"] == 2000
    assert member_node["metadata"]["storageKey"] == item["metadata"]["storageKey"]
    assert "assetId" not in member_node["metadata"]
    file_path = f"{ROOT}/{identity}/file"
    assert item["metadata"]["content"] == member_node["metadata"]["content"] == file_path
    for client in (owner,):
        assert client.get(file_path + "?proxy=1").content == body
        assert client.head(file_path).headers["Content-Length"] == str(len(body))
        partial = client.get(file_path, headers={"Range": "bytes=4-15"})
        assert partial.status_code == 206 and partial.content == body[4:16]
    assert outsider.get(file_path).status_code == 404
    with resource_app[1]() as session:
        resource = session.get(MediaFile, int(identity))
        assert resource.project_id == int(project["id"]) and resource.published_at
        assert resource.checksum_sha256 == hashlib.sha256(body).hexdigest()
        assert resource.duration_ms == 2000
        if kind == "video":
            assert resource.width == 160 and resource.height == 90
            assert item["metadata"]["hasAudio"] is True
            assert item["metadata"]["videoPreview"] == report["recovered_preview"]
            assert item["metadata"]["videoPreview"]["sourceKey"] == "resource:" + identity
            poster = item["metadata"]["videoPreview"]["storageKey"].removeprefix("resource:")
            assert member.get(f"{ROOT}/{poster}/file").status_code == 404
            assert outsider.get(f"{ROOT}/{poster}/file").status_code == 404
            poster_bytes = owner.get(f"{ROOT}/{poster}/file?proxy=1").content
            with Image.open(BytesIO(poster_bytes)) as image:
                assert image.size == (160, 90)
                red, green, blue = image.convert("RGB").getpixel((80, 45))
                assert red < 10 and green < 10 and blue > 240
        location = resource_app[0].state.storage.stat(
            getattr(resource_app[2], f"minio_{kind}_bucket"), f"canvas/resources/{identity}"
        )
        assert resource.storage_locator == location.storage_locator
        with resource_app[0].state.storage.open(location.bucket, location.object_name) as stream:
            assert stream.read() == body
        identical = list(
            session.scalars(
                select(MediaFile).where(
                    MediaFile.checksum_sha256 == resource.checksum_sha256,
                    MediaFile.format_code == resource.format_code,
                )
            )
        )
        assert len(identical) == 1
        original_uploads = list(
            session.scalars(
                select(CanvasResourceUpload).where(
                    CanvasResourceUpload.idempotency_hash
                    == hashlib.sha256(report["pending_media"]["key"].encode()).hexdigest(),
                )
            )
        )
        assert len(original_uploads) == 1
    assert "blob:" not in json.dumps(saved) and f"{kind}:{owner_user['id']}:" not in json.dumps(
        saved
    )
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "kind": kind,
                "scenario": scenario,
                "width": width,
                "failure_timing": failure_timing,
                "sha256": hashlib.sha256(body).hexdigest(),
                "browser": report,
                "python_postchecks": "passed",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
