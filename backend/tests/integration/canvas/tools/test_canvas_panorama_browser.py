"""原全景查看器、WebGL四向实际像素、永久作品与窗口/成员恢复。"""

import hashlib
import json
import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from short_drama.domain import AsyncTask, CanvasResourceUpload, MediaFile
from short_drama.domain.canvas_library import CanvasLibraryAsset, CanvasLibraryAssetReference
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.image.test_canvas_image_tools_browser import minio_bytes
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import ROOT, canvas, upload
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
        reason="Enable original panorama WebGL with isolated HTTP/MySQL/MinIO",
    ),
]


def panorama_png():
    # SphereGeometry的u=0.5/0.25/0/0.75分别对应原yaw=0/90/180/270。
    image = Image.new("RGB", (2048, 1024), (240, 40, 40))
    draw = ImageDraw.Draw(image)
    for left, right, color in (
        (256, 767, (40, 220, 80)),
        (768, 1279, (40, 80, 240)),
        (1280, 1791, (240, 220, 40)),
    ):
        draw.rectangle((left, 0, right, 1023), fill=color)
    # 细刻度让单次4度旋转也产生可核验的像素变化，方向中心仍保留纯色。
    for longitude in range(32, 2048, 64):
        for top, bottom in ((320, 448), (576, 704)):
            draw.line((longitude, top, longitude, bottom), fill=(220, 220, 220), width=3)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize("width", [1440, 600])
def test_original_panorama_viewer_exports_real_views_and_restores_shared_outputs(
    resource_app, database_errors, tmp_path, width
):
    node = shutil.which("node")
    assert node, "Node.js is required for original panorama UI verification"
    owner, owner_user = account(resource_app, "panorama_owner")
    member, member_user = account(resource_app, "panorama_member")
    outsider, _ = account(resource_app, "panorama_outsider")
    project, path, document = canvas(owner, "panorama-project")
    original = panorama_png()
    response = upload(owner, original, key="panorama-source", source=document["id"])
    assert response.status_code == 200, response.text
    source = response.json()["resource"]
    document["nodes"] = [
        {
            "id": "panorama-source",
            "type": "image",
            "title": "全景方向色板",
            "position": {"x": -350, "y": -160},
            "width": 400,
            "height": 200,
            "metadata": {
                "status": "success",
                "content": f"{ROOT}/{source['id']}/file",
                "storageKey": "resource:" + source["id"],
                "naturalWidth": 2048,
                "naturalHeight": 1024,
                "mimeType": "image/png",
            },
        }
    ]
    committed = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "panorama-source-fixture"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert committed.status_code == 200, committed.text
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    frontend = Path(__file__).resolve().parents[5] / "frontend/canvas"
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api:
        config = {
            "apiUrl": api,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "sourceResourceId": source["id"],
            "username": "panorama_owner",
            "memberUsername": "panorama_member",
            "password": PASSWORD,
            "width": width,
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-panorama-python.mjs"],
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
            pytest.fail(f"Panorama browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Panorama browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert report["step"] == "complete"
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["resource_posts"] == 2 and report["task_posts"] == 0
    assert report["created_from_original_tool"] and report["fullscreen_single_context"]
    if width >= 640:
        assert report["desktop_creation_hidden_like_source"]
    assert report["keyboard_drag_wheel"] and report["cookie_only_recovery"]
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == 4 and len(final["connections"]) == 3
    panorama = next(item for item in final["nodes"] if item["id"] == report["panorama_node_id"])
    assert panorama["type"] == "panorama"
    assert panorama["metadata"]["panoramaConfig"]["sourceMode"] == "image"
    assert panorama["metadata"]["panoramaConfig"]["projection"] == "spherical"
    assert {(edge["fromNodeId"], edge["toNodeId"]) for edge in final["connections"]} == {
        ("panorama-source", panorama["id"]),
        *((panorama["id"], item["node_id"]) for item in report["outputs"]),
    }
    assert member.get(path + "/my-document").status_code == 404
    shared = owner.get(path).json()["source_document"]
    assert all(
        item["node_id"] in {value["id"] for value in shared["nodes"]} for item in report["outputs"]
    )
    assert outsider.get(path + "/my-document").status_code == 404
    assert owner.get(f"{ROOT}/{source['id']}/file").content == original
    with resource_app[1]() as session:
        assert session.scalar(select(AsyncTask.id)) is None
        assert len(list(session.scalars(select(MediaFile.id)))) == 3
        assert len(list(session.scalars(select(CanvasResourceUpload.id)))) == 3
        for item in report["outputs"]:
            media = session.get(MediaFile, int(item["resource_id"]))
            assert media.created_by == int(owner_user["id"]) and media.project_id == int(
                project["id"]
            )
            assert media.format_code == "image/png" and media.published_at is not None
            data = minio_bytes(resource_app, media)
            assert hashlib.sha256(data).hexdigest() == media.checksum_sha256 == item["checksum"]
            assert len(data) == media.byte_size
            assert (tmp_path / "browser" / item["filename"]).read_bytes() == data
            assert owner.get(f"{ROOT}/{media.id}/file").content == data
            assert member.get(f"{ROOT}/{media.id}/file").status_code == 404
            assert outsider.get(f"{ROOT}/{media.id}/file").status_code == 404
            with Image.open(BytesIO(data)) as image:
                assert image.size == (media.width, media.height) == (item["width"], item["height"])
                if item["filename"] == "panorama-quad.png":
                    assert image.size == (2048, 2048)
                    expected = [(40, 80, 240), (40, 220, 80), (240, 40, 40), (240, 220, 40)]
                    for position, color in zip(
                        [(512, 512), (1536, 512), (512, 1536), (1536, 1536)], expected, strict=True
                    ):
                        pixel = image.convert("RGB").getpixel(position)
                        assert all(
                            abs(actual - value) <= 5
                            for actual, value in zip(pixel, color, strict=True)
                        ), pixel
                else:
                    assert image.width > 100 and image.height > 100
                    pixel = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
                    assert pixel[2] > 230 and pixel[0] < 50, pixel
            asset = session.scalar(
                select(CanvasLibraryAsset).where(CanvasLibraryAsset.source_key == item["asset_id"])
            )
            assert asset and asset.user_id == int(owner_user["id"])
            assert session.scalar(
                select(CanvasLibraryAssetReference.id).where(
                    CanvasLibraryAssetReference.library_asset_id == asset.id,
                    CanvasLibraryAssetReference.media_id == media.id,
                )
            )
