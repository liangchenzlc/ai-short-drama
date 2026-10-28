"""原画布时间线的真实浏览器FFmpeg、下载、归档及新窗口恢复。"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import CanvasRenderJob, MediaFile
from short_drama.service.canvas.timeline.timeline_renderer import CanvasTimelineRenderer
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
        reason="Enable original timeline browser/FFmpeg/HTTP/MySQL/MinIO verification",
    ),
]


@pytest.mark.parametrize(
    "width,serve_mode", [(1440, "dev"), (600, "dev"), (1440, "preview"), (600, "preview")]
)
def test_original_timeline_download_assembled_node_and_cookie_only_recovery(
    resource_app, tmp_path, width, serve_mode
):
    node = shutil.which("node")
    assert node
    owner, _ = account(resource_app, "timeline_ui_owner")
    member, member_user = account(resource_app, "timeline_ui_member")
    outsider, _ = account(resource_app, "timeline_ui_outsider")
    project, path, document = canvas(owner, "timeline-ui-project")
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
    response = upload(
        owner,
        video.read_bytes(),
        key="timeline-ui-source",
        source=document["id"],
        kind="video",
        name="source.mp4",
        mime="video/mp4",
    )
    assert response.status_code == 200, response.text
    media = response.json()["resource"]
    document["nodes"] = [
        {
            "id": "timeline-video",
            "type": "video",
            "title": "时间线来源",
            "position": {"x": -200, "y": -120},
            "width": 400,
            "height": 225,
            "metadata": {
                "status": "success",
                "content": f"{ROOT}/{media['id']}/file",
                "storageKey": "resource:" + media["id"],
                "naturalWidth": 320,
                "naturalHeight": 180,
                "hasAudio": True,
                "mimeType": "video/mp4",
                "durationMs": 1000,
                "subtitleEntries": [
                    {"index": 1, "startMs": 200, "endMs": 800, "text": "原界面中文字幕"}
                ],
            },
        }
    ]
    committed = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "timeline-ui-fixture"},
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
            "username": "timeline_ui_owner",
            "memberUsername": "timeline_ui_member",
            "password": PASSWORD,
            "width": width,
            "serveMode": serve_mode,
            "outputDirectory": str(output_directory),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-timeline-python.mjs"],
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
            pytest.fail(f"Original timeline browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
    assert process.returncode == 0, f"Original timeline browser failed: {errors}\n{output}"
    result = json.loads(output)
    assert result["timeline_saved"] and result["actual_wasm_download"]
    assert result["cookie_only_recovery"] and result["timeline_restored"]
    assert not result["page_errors"] and not result["http_errors"], result
    assert result["render_posts"] == 0, "Original canvas export uses browser FFmpeg"
    probe = CanvasTimelineRenderer(settings).probe(output_directory / "timeline-download.mp4")
    assert probe.facts.has_video and probe.facts.has_audio
    assert (probe.width, probe.height) == (1920, 1080) and 1000 <= probe.facts.duration_ms < 1100
    saved = owner.get(path + "/my-document").json()["source_document"]
    assert saved["timeline"]["clips"] and saved["timeline"]["durationMs"] == 1000
    assembled = next(item for item in saved["nodes"] if item["id"] == result["assembled_node_id"])
    key = assembled["metadata"]["storageKey"]
    assert key.startswith("resource:") and key != "resource:" + media["id"]
    resource_id = key.partition(":")[2]
    endpoint = ROOT + f"/{resource_id}/file"
    downloaded = owner.get(endpoint)
    assert downloaded.status_code == 200
    assert (member.get(endpoint).status_code == 404) and outsider.get(endpoint).status_code == 404
    assert hashlib.sha256(downloaded.content).hexdigest() == result["assembled_checksum"]
    with resource_app[1]() as session:
        stored = session.get(MediaFile, int(resource_id))
        assert (
            stored.published_at is not None
            and stored.checksum_sha256 == result["assembled_checksum"]
        )
        assert (stored.width, stored.height) == (1920, 1080)
        assert list(session.scalars(select(CanvasRenderJob))) == []
