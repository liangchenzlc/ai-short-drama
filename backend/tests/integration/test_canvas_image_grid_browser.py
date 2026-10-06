"""原宫格菜单的全部预设/自定义、奇数图像素与隔离 MySQL/MinIO；不拦截 API。"""

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

from short_drama.domain import CanvasResourceUpload, MediaFile
from short_drama.domain.canvas import CanvasEdge, CanvasNode
from short_drama.domain.canvas_library import CanvasLibraryAsset, CanvasLibraryAssetReference
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_image_tools_browser import minio_bytes, pixel_pattern_png
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable explicit real canvas browser and isolated MinIO verification",
    ),
]


def test_source_grid_all_presets_custom_cancel_refresh_pixels_and_permissions(
    resource_app, database_errors
):
    node = shutil.which("node")
    assert node, "Node.js is required for this explicit browser integration test"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir(), (
        "Run npm run install:all in frontend before real browser verification"
    )
    owner, owner_user = account(resource_app, "canvas_grid_owner")
    member, member_user = account(resource_app, "canvas_grid_member")
    outsider, _ = account(resource_app, "canvas_grid_outsider")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "grid-browser-project"},
        json={"name": "真实宫格切分验证", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    join(resource_app, owner, member, project["id"], member_user["id"])
    scenarios = []
    for index, (label, rows, columns, preset) in enumerate(
        (
            ("4宫格 (2×2)", 2, 2, True),
            ("9宫格 (3×3)", 3, 3, True),
            ("16宫格 (4×4)", 4, 4, True),
            ("25宫格 (5×5)", 5, 5, True),
            ("自定义 3×2", 2, 3, False),
        )
    ):
        canvas_id = project["primary_canvas_id"]
        if index:
            created = owner.post(
                f"/api/v1/projects/{project['id']}/canvases",
                headers={"Idempotency-Key": f"grid-browser-canvas-{index}"},
                json={"title": label},
            )
            assert created.status_code == 201, created.text
            canvas_id = created.json()["id"]
        path = f"/api/v1/projects/{project['id']}/canvases/{canvas_id}"
        initial = owner.get(path + "/my-document")
        assert initial.status_code == 200, initial.text
        assert initial.json()["source_document"]["nodes"] == []
        scenarios.append(
            {
                "label": label,
                "rows": rows,
                "columns": columns,
                "preset": preset,
                "canvasId": canvas_id,
                "sourceKey": initial.json()["source_key"],
            }
        )
    original_bytes = pixel_pattern_png()
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "username": "canvas_grid_owner",
            "memberUsername": "canvas_grid_member",
            "outsiderUsername": "canvas_grid_outsider",
            "password": PASSWORD,
            "image": base64.b64encode(original_bytes).decode(),
            "cases": scenarios,
        }
        process = subprocess.Popen(
            [node, "scripts/verify-image-grid-python.mjs"],
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
            pytest.fail(f"Canvas grid browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Canvas grid browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete"
    assert report["page_errors"] == []
    assert report["cancel_and_invalid_pick_kept_source"]
    assert len(report["groups"]) == 5
    with Image.open(BytesIO(original_bytes)) as image:
        original_pixels = image.convert("RGB")
    for group, scenario in zip(report["groups"], scenarios, strict=True):
        assert group["label"] == scenario["label"]
        assert group["saved_and_permissions_checked"] and group["fresh_context_restored"]
        rows, columns = scenario["rows"], scenario["columns"]
        count = rows * columns
        assert len(group["pieces"]) == count
        reconstructed = Image.new("RGB", (37, 19))
        path = f"/api/v1/projects/{project['id']}/canvases/{group['canvas_id']}"
        canonical = owner.get(path + "/my-document")
        assert canonical.status_code == 200, canonical.text
        document = canonical.json()["source_document"]
        assert len(document["nodes"]) == count + 1
        assert len(document["connections"]) == count
        by_id = {item["id"]: item for item in document["nodes"]}
        source = group["source_node"]
        assert by_id[source["id"]] == source
        expected_resources = [(group["source_resource_id"], original_bytes)]
        for index, piece in enumerate(group["pieces"]):
            row, column = divmod(index, columns)
            assert (piece["row"], piece["column"]) == (row, column)
            x0, x1 = column * 37 // columns, (column + 1) * 37 // columns
            y0, y1 = row * 19 // rows, (row + 1) * 19 // rows
            content = base64.b64decode(piece["bytes_base64"])
            assert hashlib.sha256(content).hexdigest() == piece["sha256"]
            with Image.open(BytesIO(content)) as decoded:
                assert decoded.format == "PNG" and decoded.size == (x1 - x0, y1 - y0)
                pixels = decoded.convert("RGB")
                assert pixels.tobytes() == original_pixels.crop((x0, y0, x1, y1)).tobytes()
                reconstructed.paste(pixels, (x0, y0))
            child = by_id[piece["node"]["id"]]
            assert child == piece["node"]
            assert child["metadata"]["generatedFromNodeId"] == source["id"]
            assert child["metadata"]["resultOrigin"] == "derived"
            assert child["metadata"]["manualSize"] is True
            assert (child["metadata"]["naturalWidth"], child["metadata"]["naturalHeight"]) == (
                x1 - x0,
                y1 - y0,
            )
            expected_resources.append((piece["resource_id"], content))
        assert reconstructed.tobytes() == original_pixels.tobytes(), "奇数尺寸尾部不得丢失"
        with resource_app[1]() as session:
            stored = list(
                session.scalars(
                    select(CanvasNode).where(CanvasNode.canvas_id == int(group["canvas_id"]))
                )
            )
            assert {item.node_key for item in stored} == set(by_id)
            edges = list(
                session.scalars(
                    select(CanvasEdge).where(CanvasEdge.canvas_id == int(group["canvas_id"]))
                )
            )
            assert {(item.from_node_key, item.to_node_key) for item in edges} == {
                (source["id"], piece["node"]["id"]) for piece in group["pieces"]
            }
            for identifier, content in expected_resources:
                media = session.get(MediaFile, int(identifier))
                assert media and media.project_id == int(project["id"]) and media.published_at
                assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
                assert media.byte_size == len(content)
                assert minio_bytes(resource_app, media) == content
                upload = session.scalar(
                    select(CanvasResourceUpload).where(CanvasResourceUpload.media_id == media.id)
                )
                assert upload and upload.user_id == int(owner_user["id"])
                assert upload.canvas_id == int(group["canvas_id"]) and upload.status == "ready"
                binding = session.scalar(
                    select(CanvasLibraryAssetReference).where(
                        CanvasLibraryAssetReference.media_id == media.id
                    )
                )
                assert binding
                asset = session.get(CanvasLibraryAsset, binding.library_asset_id)
                assert asset and asset.user_id == int(owner_user["id"])
                resource = f"/api/v1/canvas-runtime/resources/{identifier}/file"
                assert owner.get(resource).content == member.get(resource).content == content
                assert outsider.get(resource).status_code == 404
                assert (
                    member.get(f"/api/v1/canvas-runtime/assets/{asset.source_key}").status_code
                    == 404
                )
        assert outsider.get(path + "/my-document").status_code == 404
