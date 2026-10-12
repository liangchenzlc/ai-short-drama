"""原图片标注 UI、真实 API/MySQL/MinIO；验证加工、恢复与共享边界。"""

import base64
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

from short_drama.domain import MediaFile
from short_drama.domain.canvas import CanvasEdge, CanvasNode
from short_drama.domain.canvas_library import CanvasLibraryAsset, CanvasLibraryAssetReference
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_image_tools_browser import minio_bytes
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original annotation UI with isolated MySQL/MinIO",
    ),
]


def annotation_original() -> bytes:
    image = Image.new("RGB", (320, 200), (32, 48, 64))
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_original_annotation_tools_history_bytes_refresh_and_permissions(
    resource_app, database_errors, tmp_path
):
    node = shutil.which("node")
    assert node, "Node.js is required for this explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, owner_user = account(resource_app, "annotation_browser_owner")
    member, member_user = account(resource_app, "annotation_browser_member")
    outsider, _ = account(resource_app, "annotation_browser_outsider")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "annotation-browser-project"},
        json={"name": "原标注加工验证", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document")
    assert initial.status_code == 200, initial.text
    assert initial.json()["source_document"]["nodes"] == []
    join(resource_app, owner, member, project["id"], member_user["id"])
    original = annotation_original()
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial.json()["source_key"],
            "username": "annotation_browser_owner",
            "memberUsername": "annotation_browser_member",
            "outsiderUsername": "annotation_browser_outsider",
            "password": PASSWORD,
            "image": base64.b64encode(original).decode(),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-image-annotation-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Annotation browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Annotation browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    for field in (
        "cancel_kept_original",
        "tools_and_history_checked",
        "derived_graph_saved",
        "fresh_context_and_permissions_checked",
    ):
        assert report[field], field
    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["failed_routes"] == [] and report["resource_posts"] == 2
    document = owner.get(path + "/my-document").json()["source_document"]
    assert len(document["nodes"]) == 2 and len(document["connections"]) == 1
    with resource_app[1]() as session:
        stored_nodes = list(
            session.scalars(
                select(CanvasNode).where(CanvasNode.canvas_id == int(project["primary_canvas_id"]))
            )
        )
        assert {node.node_key for node in stored_nodes} == {
            report["source"]["node_id"],
            report["child"]["node_id"],
        }
        edge = session.scalar(
            select(CanvasEdge).where(CanvasEdge.canvas_id == int(project["primary_canvas_id"]))
        )
        assert (edge.from_node_key, edge.to_node_key) == (
            report["source"]["node_id"],
            report["child"]["node_id"],
        )
        for field in ("source", "child"):
            item = report[field]
            media = session.get(MediaFile, int(item["resource_id"]))
            assert media.created_by == int(owner_user["id"]) and media.published_at
            assert (media.width, media.height) == (320, 200)
            assert media.format_code == "image/png"
            content = minio_bytes(resource_app, media)
            assert media.byte_size == len(content)
            assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
            asset = session.scalar(
                select(CanvasLibraryAsset).where(CanvasLibraryAsset.source_key == item["asset_id"])
            )
            assert asset.user_id == int(owner_user["id"])
            reference = session.scalar(
                select(CanvasLibraryAssetReference).where(
                    CanvasLibraryAssetReference.library_asset_id == asset.id,
                    CanvasLibraryAssetReference.media_id == media.id,
                )
            )
            assert reference is not None
            if field == "source":
                assert content == original
                continue
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            with Image.open(BytesIO(content)) as rendered:
                pixels = rendered.convert("RGB")
                assert pixels.size == (320, 200)
                assert pixels.getpixel((0, 0)) == (32, 48, 64)
                assert pixels.getpixel((60, 40)) == (239, 68, 68)
                assert pixels.getpixel((200, 40)) == (34, 197, 94)
                assert pixels.getpixel((150, 65)) == (34, 197, 94)
                assert pixels.getpixel((200, 65)) == (32, 48, 64)
                text = pixels.crop((40, 125, 230, 185))
                assert sum(pixel == (59, 130, 246) for pixel in text.getdata()) > 100
    assert database_errors == []
