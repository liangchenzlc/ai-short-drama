"""原多选合并与字幕 SRT 的浏览器、永久媒体和共享权限验证。"""

import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.domain import AsyncTask, MediaFile
from short_drama.service.canvas_timeline_renderer import CanvasTimelineRenderer
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_resources import ROOT, canvas, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_timeline_ffmpeg import ffmpeg
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_TIMELINE_FFMPEG") != "1",
        reason="Enable original composition browser/FFmpeg/HTTP/MySQL/MinIO verification",
    ),
]


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize("kind", ["merge", "subtitles"])
def test_original_media_composition_and_subtitle_sync_survive_cookie_only_recovery(
    resource_app, tmp_path, width, kind
):
    node = shutil.which("node")
    assert node
    owner, _ = account(resource_app, "composition_owner")
    member, member_user = account(resource_app, "composition_member")
    outsider, _ = account(resource_app, "composition_outsider")
    project, path, document = canvas(owner, "composition-project")
    join(resource_app, owner, member, project["id"], member_user["id"])
    sources = []
    document["nodes"] = []
    specifications = [("red", 220, 2), ("blue", 440, 1)] if kind == "merge" else [("red", 220, 2)]
    for index, (color, frequency, duration) in enumerate(specifications, 1):
        video = tmp_path / f"source-{index}.mp4"
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x180:r=30:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            video,
        )
        response = upload(
            owner,
            video.read_bytes(),
            key=f"composition-source-{index}",
            source=document["id"],
            kind="video",
            name=video.name,
            mime="video/mp4",
        )
        assert response.status_code == 200, response.text
        media = response.json()["resource"]
        sources.append((media, video.read_bytes()))
        metadata = {
            "status": "success",
            "content": f"{ROOT}/{media['id']}/file",
            "storageKey": "resource:" + media["id"],
            "naturalWidth": 320,
            "naturalHeight": 180,
            "durationMs": duration * 1000,
            "hasAudio": True,
            "mimeType": "video/mp4",
        }
        if kind == "subtitles":
            metadata["subtitleEntries"] = [
                {"index": 1, "startMs": 200, "endMs": 800, "text": "旧字幕"}
            ]
        document["nodes"].append(
            {
                "id": f"composition-video-{index}",
                "type": "video",
                "title": f"组合来源{index}",
                "position": {"x": (index - 1) * 496, "y": -120},
                "width": 400,
                "height": 225,
                "metadata": metadata,
            }
        )
    committed = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "composition-fixture"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert committed.status_code == 200, committed.text
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    output_directory = tmp_path / "output" / "playwright"
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "sourceNodeIds": [item["id"] for item in document["nodes"]],
            "username": "composition_owner",
            "memberUsername": "composition_member",
            "password": PASSWORD,
            "width": width,
            "kind": kind,
            "outputDirectory": str(output_directory),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-media-composition-python.mjs"],
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
            pytest.fail(f"Original media composition browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
    assert process.returncode == 0, f"Original media composition browser failed: {errors}\n{output}"
    result = json.loads(output)
    assert result["step"] == "complete" and result["cookie_only_recovery"]
    assert not result["page_errors"] and not result["http_errors"] and result["task_posts"] == 0
    saved = owner.get(path + "/my-document").json()["source_document"]
    for index, (media, original_bytes) in enumerate(sources, 1):
        source = next(item for item in saved["nodes"] if item["id"] == f"composition-video-{index}")
        assert source["metadata"]["storageKey"] == "resource:" + media["id"]
        endpoint = f"{ROOT}/{media['id']}/file"
        assert owner.get(endpoint).content == original_bytes
        assert member.get(endpoint).content == original_bytes
        assert outsider.get(endpoint).status_code == 404
    with resource_app[1]() as session:
        assert list(session.scalars(select(AsyncTask))) == []
        if kind == "subtitles":
            assert result["srt_round_trip"] and result["cancel_preserved"]
            assert result["clear_persisted_without_save"]
            assert result["saved_entries"] == [
                {"index": 1, "startMs": 200, "endMs": 600, "text": "首句中文字幕"},
                {"index": 2, "startMs": 900, "endMs": 1400, "text": "末句中文字幕"},
            ]
            assert saved["nodes"][0]["metadata"]["subtitleEntries"] == []
            assert len(saved["nodes"]) == 1
            assert len(saved["timeline"]["clips"]) == 1
            assert saved["timeline"]["clips"][0]["kind"] == "video"
            assert saved["timeline"]["durationMs"] == 2000
            assert (output_directory / "exported.srt").read_text(encoding="utf-8") == (
                "1\n00:00:00,200 --> 00:00:00,600\n首句中文字幕\n\n"
                "2\n00:00:00,900 --> 00:00:01,400\n末句中文字幕\n"
            )
            return
        derived = next(item for item in saved["nodes"] if item["id"] == result["output_node_id"])
        assert len(saved["nodes"]) == 3
        for index in (1, 2):
            assert any(
                edge["fromNodeId"] == f"composition-video-{index}"
                and edge["toNodeId"] == derived["id"]
                for edge in saved["connections"]
            )
        endpoint = f"{ROOT}/{result['output_resource_id']}/file"
        downloaded = owner.get(endpoint)
        assert downloaded.status_code == 200
        assert member.get(endpoint).content == downloaded.content
        assert outsider.get(endpoint).status_code == 404
        assert hashlib.sha256(downloaded.content).hexdigest() == result["output_checksum"]
        stored = session.get(MediaFile, int(result["output_resource_id"]))
        assert stored.published_at is not None
        assert stored.checksum_sha256 == result["output_checksum"]
        artifact = output_directory / "merged.mp4"
        assert artifact.read_bytes() == downloaded.content
        probe = CanvasTimelineRenderer(settings).probe(artifact)
        assert probe.facts.has_video and probe.facts.has_audio
        assert (probe.width, probe.height) == (320, 180)
        assert 2950 <= probe.facts.duration_ms < 3200
        for second, channel, frequency in [(0.5, 0, 220), (2.5, 2, 440)]:
            frame = tmp_path / f"merged-{second}.png"
            ffmpeg("-ss", str(second), "-i", artifact, "-frames:v", "1", frame)
            pixel = Image.open(frame).convert("RGB").getpixel((160, 90))
            assert pixel[channel] >= 245 and pixel[2 if channel == 0 else 0] <= 10, pixel
            audio = tmp_path / f"merged-{second}.pcm"
            ffmpeg(
                "-ss",
                str(second),
                "-i",
                artifact,
                "-t",
                "0.25",
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "s16le",
                audio,
            )
            samples = struct.unpack(f"<{audio.stat().st_size // 2}h", audio.read_bytes())
            amplitudes = [
                math.hypot(
                    sum(
                        value * math.sin(2 * math.pi * candidate * index / 16000)
                        for index, value in enumerate(samples)
                    ),
                    sum(
                        value * math.cos(2 * math.pi * candidate * index / 16000)
                        for index, value in enumerate(samples)
                    ),
                )
                for candidate in (frequency, 660 - frequency)
            ]
            assert amplitudes[0] > 5 * amplitudes[1], amplitudes
