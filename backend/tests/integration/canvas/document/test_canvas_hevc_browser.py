"""可信原视频本地派生HEVC：原上传/播放、真实MySQL/MinIO与仅cookies恢复。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from short_drama.domain import MediaFile
from short_drama.storage.models import ObjectLocation
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.generation.test_canvas_generation_runtime import records
from tests.integration.canvas.resources.test_canvas_resources import ROOT, canvas
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
                "RUN_CANVAS_HEVC_SAMPLE",
            )
        ),
        reason="Explicit trusted HEVC sample, isolated MinIO and original browser opt-in required",
    ),
]


@pytest.mark.parametrize("width", [1440, 390])
def test_original_canvas_uploads_and_plays_trusted_hevc_without_changing_original_bytes(
    resource_app, tmp_path, width
):
    app, factory, original_settings = resource_app
    reference = Path(os.environ.get("CANVAS_HEVC_SAMPLE_PATH", ""))
    assert reference.is_file(), "Provide the provenance-checked local HEVC sample"
    source = reference.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    assert digest == "305a03da5beaa4a27f9f2e8953578f5aa1f538b8688a2056c9dd435ed1b9aa83"
    probe_path = Path(".tools/ffmpeg/probe-5.1/bin/ffprobe.exe").resolve()
    assert probe_path.is_file()
    inspected = subprocess.run(
        [
            str(probe_path),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,codec_tag_string,codec_type,width,height:format=duration",
            "-of",
            "json",
            str(reference.resolve()),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    probe = json.loads(inspected.stdout)
    video = next(row for row in probe["streams"] if row["codec_type"] == "video")
    assert video["codec_name"] == "hevc" and video["codec_tag_string"] == "hvc1"
    assert (video["width"], video["height"]) == (1280, 720)
    assert float(probe["format"]["duration"]) == 4.48
    owner, owner_user = account(resource_app, "hevc_owner_" + str(width))
    outsider, _ = account(resource_app, "hevc_outsider_" + str(width))
    project, path, document = canvas(owner, "hevc-browser-" + uuid4().hex)
    document["nodes"], document["connections"] = [], []
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "hevc-empty"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200
    settings = original_settings.model_copy(
        update={"public_origin": "http://127.0.0.1:4198", "render_ffprobe_path": str(probe_path)}
    )
    config = {
        "username": owner_user["username"],
        "password": PASSWORD,
        "sourceKey": document["id"],
        "path": path,
        "width": width,
        "media": base64.b64encode(source).decode(),
        "sha256": digest,
        "outputDirectory": str(tmp_path / "browser"),
    }
    with isolated_api(factory, settings, app.state.storage) as api_url:
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-hevc-python.mjs"],
            cwd=Path(__file__).resolve().parents[5] / "frontend/canvas",
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
            pytest.fail("Original HEVC browser timed out: " + errors + "\n" + output)
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, "Original HEVC browser failed: " + errors + "\n" + output
        browser = json.loads(output)
        assert browser["step"] == "complete" and browser["width"] == width
        assert browser["page_errors"] == browser["http_errors"] == []
        assert browser["task_posts"] == 0 and browser["video_uploads"] == 1
        assert browser["cookies_only_restored_without_reupload"]
        assert browser["playback_variants_requested"] == []
    identifier = browser["resource_id"]
    preview = browser["node"]["metadata"]["videoPreview"]
    poster_id = preview["storageKey"].removeprefix("resource:")
    poster = owner.get(f"{ROOT}/{poster_id}/file")
    assert poster.status_code == 200
    poster_bytes = poster.content
    assert hashlib.sha256(poster_bytes).hexdigest() == browser["poster_sha256"]
    (tmp_path / "poster-image.bin").write_bytes(poster_bytes)
    with Image.open(tmp_path / "poster-image.bin") as image:
        assert image.size == (preview["width"], preview["height"]) == (400, 225)
    with factory() as session:
        for media_id, expected, bucket in (
            (identifier, source, settings.minio_video_bucket),
            (poster_id, poster_bytes, settings.minio_image_bucket),
        ):
            media = session.get(MediaFile, int(media_id))
            assert media.project_id == int(project["id"]) and media.published_at is not None
            assert media.created_by == int(owner_user["id"])
            assert media.checksum_sha256 == hashlib.sha256(expected).hexdigest()
            assert media.byte_size == len(expected)
            location = ObjectLocation.parse(media.storage_locator, {bucket})
            with app.state.storage.open(location.bucket, location.object_name) as stream:
                assert stream.read() == expected
        media = session.get(MediaFile, int(identifier))
        assert (media.width, media.height, media.duration_ms) == (1280, 720, 4480)
        assert media.video_metadata["has_audio"] is True
        assert media.video_metadata["fps"] == "24/1"
    for media_id, expected in ((identifier, source), (poster_id, poster_bytes)):
        media_path = f"{ROOT}/{media_id}/file"
        assert owner.get(media_path).content == expected
        assert outsider.get(media_path).status_code == 404
    shared = owner.get(path).json()["source_document"]
    assert len(shared["nodes"]) == 1 and shared["connections"] == []
    assert shared["nodes"][0]["id"] == browser["node"]["id"]
    assert shared["nodes"][0]["metadata"]["storageKey"] == "resource:" + identifier
    assert outsider.get(path).status_code == 404
    assert records(resource_app, owner_user) == {"tasks": 0, "records": 0, "bindings": 0}
    (tmp_path / "original-hevc.mp4").write_bytes(source)
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "scope": "original canvas HEVC upload/playback with native API/MySQL/MinIO",
                "api_lifespan": "off: existing isolated authenticated API fixture",
                "width": width,
                "browser": browser,
                "probe": probe,
                "process_pid": process.pid,
                "api_url": api_url,
                "python_postchecks": "passed",
                "supplier_calls": 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
