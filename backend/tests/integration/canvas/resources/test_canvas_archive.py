"""通过宿主归档 UI 验证开发／生产入口、跨账号媒体及多画布恢复。"""

import base64
import json
import os
import shutil
import subprocess
import wave
from io import BytesIO
from pathlib import Path

import pytest

from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.resources.test_canvas_resource_copy import executable
from tests.integration.canvas.resources.test_canvas_resources import account, png
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable explicit isolated archive browser verification",
    ),
]


@pytest.mark.parametrize(
    ("runtime_mode", "archive_width"), [("development", 1440), ("production", 600)]
)
def test_original_archive_drawing_with_real_storage(
    resource_app, tmp_path, runtime_mode, archive_width
):
    audio = BytesIO()
    with wave.open(audio, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(bytes(16000))
    video = tmp_path / "archive.mp4"
    rendered = subprocess.run(
        [
            executable(resource_app[2].render_ffmpeg_path),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=64x48:rate=10",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert rendered.returncode == 0, rendered.stderr.decode(errors="replace")
    media = [
        {
            "kind": kind,
            "name": name,
            "mimeType": mime,
            "content": base64.b64encode(body).decode("ascii"),
        }
        for kind, name, mime, body in (
            ("image", "archive.png", "image/png", png()),
            ("audio", "archive.wav", "audio/wav", audio.getvalue()),
            ("video", "archive.mp4", "video/mp4", video.read_bytes()),
        )
    ]
    for username in ("canvas_archive_owner", "canvas_archive_importer"):
        client, _ = account(resource_app, username)
        client.post("/api/v1/auth/logout")
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4189"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-archive-python.mjs"],
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
            output, errors = process.communicate(
                json.dumps(
                    {
                        "apiUrl": api_url,
                        "username": "canvas_archive_owner",
                        "importer": "canvas_archive_importer",
                        "password": PASSWORD,
                        "media": media,
                        "runtimeMode": runtime_mode,
                        "archiveWidth": archive_width,
                    }
                ),
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Archive browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Archive browser failed: {errors}\n{output}"
        result = json.loads(output)
        assert result["source_zip_contains_drawing"]
        assert result["concurrent_export_kept_one_version"]
        assert result["zip_import_atomic_drawing"] and result["fresh_import_editable"]
        assert result["unknown_import_replayed_once"]
        assert result["multi_canvas_resume_kept_group"]
        assert result["runtime_mode"] == runtime_mode
        assert result["archive_width"] == archive_width
        assert result["archive_no_horizontal_overflow"]
        if runtime_mode == "production":
            assert result["production_bridge_loaded"]
        assert result["invalid_zip_rejected_before_writes"]
        assert result["zip_image_and_library_binding_restored"]
        assert result["zip_audio_video_and_library_bindings_restored"]
        assert result["page_errors"] == []
