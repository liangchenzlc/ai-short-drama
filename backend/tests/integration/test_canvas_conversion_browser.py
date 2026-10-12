"""原灰度/Canny算法、永久结果、仅cookies恢复与三方权限；不使用模型替身。"""

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

from short_drama.domain import AsyncTask, CanvasResourceUpload, MediaFile
from short_drama.domain.canvas import CanvasNode
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_image_tools_browser import minio_bytes, pixel_pattern_png
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import ROOT, canvas, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original conversion UI with isolated HTTP/MySQL/MinIO",
    ),
]


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize("operation", ["grayscale", "edge-canny"])
def test_original_conversion_archives_pixels_and_restores_author_and_member(
    resource_app, database_errors, tmp_path, width, operation
):
    node = shutil.which("node")
    assert node, "Node.js is required for original conversion UI verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend/canvas"
    owner, _ = account(resource_app, "conversion_owner")
    member, user = account(resource_app, "conversion_member")
    outsider, _ = account(resource_app, "conversion_outsider")
    project, path, document = canvas(owner, "conversion-project")
    join(resource_app, owner, member, project["id"], user["id"])
    original = pixel_pattern_png()
    source = upload(owner, original, source=document["id"], key="conversion-source")
    assert source.status_code == 200, source.text
    source = source.json()["resource"]
    document["nodes"] = [
        {
            "id": "conversion-source",
            "type": "image",
            "title": "转换来源",
            "position": {"x": -370, "y": -180},
            "width": 240,
            "height": 160,
            "metadata": {
                "storageKey": "resource:" + source["id"],
                "content": f"{ROOT}/{source['id']}/file",
                "mimeType": "image/png",
                "bytes": len(original),
                "naturalWidth": 37,
                "naturalHeight": 19,
            },
        },
        {
            "id": "conversion-target",
            "type": "media-conversion",
            "title": "转换",
            "position": {"x": -20, "y": -180},
            "width": 340,
            "height": 430,
            "metadata": {},
        },
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "conversion-fixture"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api:
        config = {
            "apiUrl": api,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "conversion_owner",
            "memberUsername": "conversion_member",
            "password": PASSWORD,
            "width": width,
            "operation": operation,
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-conversion-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=240)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Conversion browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Conversion browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert report["step"] == "complete" and report["operation"] == operation
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["resource_posts"] == 1 and report["task_posts"] == 0
    assert report["cookie_only_recovery"] and report["member_result_seen"]
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == 2 and len(final["connections"]) == 1
    converted = next(item for item in final["nodes"] if item["id"] == "conversion-target")
    state = converted["metadata"]["mediaConversion"]
    assert state["status"] == "completed" and state["operation"] == operation
    identifier = report["resource_id"]
    assert (
        state["resultStorageKey"] == converted["metadata"]["storageKey"] == "resource:" + identifier
    )
    data = owner.get(f"{ROOT}/{identifier}/file").content
    assert hashlib.sha256(data).hexdigest() == report["checksum"]
    with Image.open(BytesIO(data)) as result, Image.open(BytesIO(original)) as image:
        assert result.size == image.size == (37, 19)
        pixels = list(result.convert("RGB").getdata())
        assert all(red == green == blue for red, green, blue in pixels)
        if operation == "grayscale":
            expected = [
                int(red * 0.299 + green * 0.587 + blue * 0.114 + 0.5)
                for red, green, blue in image.convert("RGB").getdata()
            ]
            assert [red for red, _, _ in pixels] == expected
        else:
            assert {red for red, _, _ in pixels} == {0, 255}
    assert owner.get(f"{ROOT}/{source['id']}/file").content == original
    assert member.get(f"{ROOT}/{identifier}/file").content == data
    assert outsider.get(f"{ROOT}/{identifier}/file").status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404
    shared = member.get(path + "/my-document").json()["source_document"]
    shared_state = next(item for item in shared["nodes"] if item["id"] == converted["id"])[
        "metadata"
    ]["mediaConversion"]
    assert shared_state["status"] == "completed" and "startedAt" not in shared_state
    with resource_app[1]() as session:
        media = session.get(MediaFile, int(identifier))
        assert (
            media.checksum_sha256 == report["checksum"] and minio_bytes(resource_app, media) == data
        )
        assert media.project_id == int(project["id"])
        assert session.scalar(select(AsyncTask.id)) is None
        assert len(list(session.scalars(select(MediaFile.id)))) == 2
        assert len(list(session.scalars(select(CanvasResourceUpload.id)))) == 2
        stored = session.scalar(select(CanvasNode).where(CanvasNode.node_key == converted["id"]))
        assert stored.content_json["metadata"]["mediaConversion"] == shared_state
