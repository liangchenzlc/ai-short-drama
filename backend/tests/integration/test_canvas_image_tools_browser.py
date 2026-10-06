"""原裁切 UI、认证 HTTP、隔离 MySQL/MinIO；仅两个失败阶段注入 HTTP 503。"""

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
from short_drama.domain.canvas import CanvasNode
from short_drama.domain.canvas_library import CanvasLibraryAsset, CanvasLibraryAssetReference
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_browser import isolated_api, stop_browser
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


def pixel_pattern_png() -> bytes:
    """奇数尺寸、每个像素不同；裁切可独立核对位置、边界与全部像素。"""
    image = Image.new("RGB", (37, 19))
    image.putdata(
        [
            ((x * 7) % 256, (y * 13) % 256, (x * 5 + y * 9) % 256)
            for y in range(19)
            for x in range(37)
        ]
    )
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def minio_bytes(resource_app, media: MediaFile) -> bytes:
    settings = resource_app[2]
    location = ObjectLocation.parse(
        media.storage_locator,
        {settings.minio_image_bucket, settings.minio_video_bucket, settings.minio_audio_bucket},
    )
    assert location.bucket == settings.minio_image_bucket
    with resource_app[0].state.storage.open(location.bucket, location.object_name) as response:
        return response.read()


def test_source_crop_ui_bytes_refresh_failure_drafts_and_project_permissions(
    resource_app, database_errors
):
    node = shutil.which("node")
    assert node, "Node.js is required for this explicit browser integration test"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir(), (
        "Run npm run install:all in frontend before real browser verification"
    )
    owner, owner_user = account(resource_app, "canvas_crop_owner")
    member, member_user = account(resource_app, "canvas_crop_member")
    outsider, _ = account(resource_app, "canvas_crop_outsider")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "crop-browser-project"},
        json={"name": "真实图片裁切验证", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document")
    assert initial.status_code == 200, initial.text
    source_key = initial.json()["source_key"]
    join(resource_app, owner, member, project["id"], member_user["id"])
    original_bytes = pixel_pattern_png()
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": source_key,
            "username": "canvas_crop_owner",
            "memberUsername": "canvas_crop_member",
            "outsiderUsername": "canvas_crop_outsider",
            "password": PASSWORD,
            "image": base64.b64encode(original_bytes).decode(),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-image-tools-python.mjs"],
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
            pytest.fail(f"Canvas crop browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Canvas crop browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    for field in (
        "cancel_kept_source",
        "crop_saved",
        "fresh_context_restored",
        "commit_failure_kept_draft",
        "commit_failure_no_shared_product",
        "commit_failure_recovered_same_child",
        "upload_failure_kept_local_bytes",
        "upload_failure_no_saved_product",
        "permissions_checked",
    ):
        assert report[field], field
    assert report["page_errors"] == []
    assert {item["stage"] for item in report["failure_injections"]} == {
        "canvas_commit",
        "resource_upload",
    }
    for stage, code in (
        (path + "/commits", "test_canvas_commit_unavailable"),
        ("/api/v1/canvas-runtime/resources", "test_resource_upload_unavailable"),
    ):
        assert any(
            item["path"] == stage and item["status"] == 503 and item.get("code") == code
            for item in report["http"]
        ), (stage, report["http"])
    assert report["source_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    derived_bytes = base64.b64decode(report["crop_bytes_base64"])
    assert report["crop_sha256"] == hashlib.sha256(derived_bytes).hexdigest()
    with (
        Image.open(BytesIO(original_bytes)) as original,
        Image.open(BytesIO(derived_bytes)) as cropped,
    ):
        assert cropped.format == "PNG" and cropped.size == (30, 16)
        # 已知 source 默认 .1/.1/.8/.8 在 37×19 的期望边界；不调用目标算法生成预期值。
        assert cropped.convert("RGB").tobytes() == original.crop((3, 1, 33, 17)).tobytes()

    canonical = owner.get(path + "/my-document")
    assert canonical.status_code == 200, canonical.text
    document = canonical.json()["source_document"]
    assert len(document["nodes"]) == 3
    assert len(document["connections"]) == 2
    by_id = {item["id"]: item for item in document["nodes"]}
    source = by_id[report["source_node_id"]]
    assert report["upload_failure_node_id"] not in by_id
    for key in ("crop_node_id", "commit_failure_node_id"):
        child = by_id[report[key]]
        assert child["position"] == {
            "x": source["position"]["x"] + source["width"] + 96,
            "y": source["position"]["y"],
        }
        assert child["metadata"]["resultOrigin"] == "derived"
        assert child["metadata"]["generatedFromNodeId"] == source["id"]
        assert (child["metadata"]["naturalWidth"], child["metadata"]["naturalHeight"]) == (30, 16)

    resource_root = "/api/v1/canvas-runtime/resources"
    with resource_app[1]() as session:
        stored_nodes = list(
            session.scalars(
                select(CanvasNode).where(CanvasNode.canvas_id == int(project["primary_canvas_id"]))
            )
        )
        assert {item.node_key for item in stored_nodes} == set(by_id)
        for key, expected in (
            ("source_resource_id", original_bytes),
            ("crop_resource_id", derived_bytes),
            ("commit_failure_resource_id", derived_bytes),
        ):
            identifier = report[key]
            media = session.get(MediaFile, int(identifier))
            assert media and media.project_id == int(project["id"]) and media.published_at
            assert media.checksum_sha256 == hashlib.sha256(expected).hexdigest()
            assert media.byte_size == len(expected)
            assert minio_bytes(resource_app, media) == expected
            upload = session.scalar(
                select(CanvasResourceUpload).where(CanvasResourceUpload.media_id == media.id)
            )
            assert upload and upload.user_id == int(owner_user["id"])
            assert upload.canvas_id == int(project["primary_canvas_id"])
            assert upload.status == "ready"
            binding = session.scalar(
                select(CanvasLibraryAssetReference).where(
                    CanvasLibraryAssetReference.media_id == media.id
                )
            )
            assert binding
            asset = session.get(CanvasLibraryAsset, binding.library_asset_id)
            assert asset and asset.user_id == int(owner_user["id"])
            assert owner.get(f"{resource_root}/{identifier}/file").content == expected
            assert member.get(f"{resource_root}/{identifier}/file").content == expected
            assert outsider.get(f"{resource_root}/{identifier}/file").status_code == 404
            assert (
                member.get(f"/api/v1/canvas-runtime/assets/{asset.source_key}").status_code == 404
            )
    assert outsider.get(path + "/my-document").status_code == 404
