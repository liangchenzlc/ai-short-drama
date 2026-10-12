"""真实角色菜单、保存及成员读取；随机 MySQL/MinIO，无模型调用。"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import func, select

from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AssetVersion,
    AsyncTask,
    CanvasResourceCopySource,
    CanvasResourceUpload,
    MediaFile,
)
from short_drama.domain.canvas import CanvasMediaReference, CanvasNode, CanvasNodeUserState
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_library import text_asset
from tests.integration.test_canvas_resources import canvas, png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_source_project_binding import business, seed_binding
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="显式启用真实角色浏览器、MySQL 与 MinIO 验证",
    ),
]


def _read(response, status):
    assert response.status_code == status, response.text
    return response.json()


def _source_state(factory, asset_id):
    with factory() as session:
        asset = session.get(Asset, int(asset_id))
        version = session.get(AssetVersion, asset.current_version_id)
        media = session.get(MediaFile, asset.media_id)
        return {
            "row_version": asset.row_version,
            "version_id": asset.current_version_id,
            "version_count": session.scalar(
                select(func.count())
                .select_from(AssetVersion)
                .where(AssetVersion.asset_id == asset.id)
            ),
            "snapshot": version.snapshot_json,
            "hash": version.content_hash,
            "media_id": media.id,
            "media_locator": media.storage_locator,
            "media_hash": media.checksum_sha256,
        }


def _assert_png(storage, settings, media, content):
    assert (media.width, media.height) == (37, 19)
    assert media.format_code == "image/png" and media.byte_size == len(content)
    assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
    location = ObjectLocation.parse(media.storage_locator, {settings.minio_image_bucket})
    stored = storage.stat(location.bucket, location.object_name)
    assert stored.size == len(content) and stored.content_type == "image/png"
    with storage.open(location.bucket, location.object_name) as stream:
        assert stream.read() == content


@pytest.mark.parametrize("width", [1440, 600])
def test_current_character_original_menus_copy_save_and_cookie_recovery(
    resource_app, database_errors, tmp_path, request, width
):
    node = shutil.which("node")
    assert node, "真实浏览器验证需要 Node.js"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner_name, member_name = f"role_owner_{width}", f"role_member_{width}"
    owner, owner_user = account(resource_app, owner_name)
    member, member_user = account(resource_app, member_name)
    source = business(owner, "角色来源")
    project, path, _ = canvas(owner, f"role-browser-{width}")
    seed_binding(resource_app, project, source)
    for scope in (source, project):
        join(resource_app, owner, member, scope["id"], member_user["id"])
    asset = _read(
        owner.post(
            f"/api/v1/projects/{source['id']}/assets",
            headers={"Idempotency-Key": f"role-browser-asset-{width}"},
            json={
                "kind": "character",
                "name": "阿青",
                "description": "青衣少女",
                "prompt": "保持青衣",
            },
        ),
        201,
    )
    content = png("purple")
    candidate = _read(
        owner.post(
            f"/api/v1/assets/{asset['id']}/image-candidates/upload",
            files={"file": ("role-source.png", content, "image/png")},
        ),
        201,
    )
    adopted = _read(
        owner.post(
            f"/api/v1/assets/{asset['id']}/confirm",
            json={
                "row_version": asset["row_version"],
                "media_id": candidate["media_id"],
                "expected_media_id": None,
            },
        ),
        200,
    )
    assert adopted["state"] == "confirmed" and adopted["media_id"] == candidate["media_id"]
    private_asset = f"role-private-library-{width}"
    text_asset(owner, private_asset)
    baseline = _source_state(resource_app[1], asset["id"])
    initial = _read(owner.get(path + "/my-document"), 200)
    assert initial["source_document"]["nodes"] == []
    assert initial["source_project_id"] == source["id"]
    assert initial["source_document"]["projectId"] == source["id"]
    assert initial["source_document"]["workspaceProjectId"] == project["id"]
    assert initial["id"] == project["primary_canvas_id"]
    catalog = _read(
        owner.get(
            f"/api/v1/canvas-runtime/canvas-projects/{initial['source_key']}/character-catalog"
        ),
        200,
    )
    assert len(catalog["items"]) == 1 and catalog["items"][0]["asset_id"] == asset["id"]
    current_version = catalog["items"][0]["current_version_id"]
    assert current_version == str(baseline["version_id"])
    assert owner.post("/api/v1/auth/logout").status_code == 200
    assert member.post("/api/v1/auth/logout").status_code == 200
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    settings = resource_app[2].model_copy(update={"public_origin": f"http://127.0.0.1:{port}"})
    resources = {"python_pid": os.getpid(), "vite_port": port, "viewport_width": width}
    request.node.user_properties.append(("character_browser_resources", json.dumps(resources)))
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        resources["api_port"] = urlsplit(api_url).port
        config = {
            "apiUrl": api_url,
            "port": port,
            "width": width,
            "username": owner_name,
            "memberUsername": member_name,
            "password": PASSWORD,
            "sourceKey": initial["source_key"],
            "sourceProjectId": source["id"],
            "owningProjectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "documentPath": path,
            "assetId": asset["id"],
            "versionId": current_version,
            "sourceMediaId": candidate["media_id"],
            "privateAssetId": private_asset,
            "imageSha256": hashlib.sha256(content).hexdigest(),
            "output": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-character-current-python.mjs"],
            cwd=frontend,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        resources["node_pid"] = process.pid
        request.node.user_properties.append(("character_browser_resources", json.dumps(resources)))
        try:
            output, errors = process.communicate(json.dumps(config), timeout=240)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"角色浏览器超时：{errors}\n{output}\n产物：{tmp_path}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        report_path = tmp_path / "browser" / "report.json"
        assert report_path.is_file(), f"缺少浏览器终态：{errors}\n{output}"
        result = json.loads(report_path.read_text(encoding="utf-8"))
        resources.update(result.get("resources", {}))
        request.node.user_properties.append(("character_browser_resources", json.dumps(resources)))
        assert process.returncode == 0, (
            f"角色浏览器失败：{errors}\n{output}\n数据库：{database_errors}\n产物：{tmp_path}"
        )
        assert result["menus"] == ["studio", "add", "context"]
        for outcome in (
            "original_details",
            "cookie_only_restored",
            "member_work_visible",
            "private_isolated",
        ):
            assert result[outcome] is True
        assert result["provider_requests"] == 0
    for closed_port in (port, resources["api_port"]):
        with socket.socket() as listener:
            listener.settimeout(1)
            assert listener.connect_ex(("127.0.0.1", closed_port)) != 0, (
                f"本测试端口仍在监听：{closed_port}"
            )
    assert _source_state(resource_app[1], asset["id"]) == baseline
    copy_id = int(result["copy_id"])
    assert copy_id != int(candidate["media_id"])
    with resource_app[1]() as session:
        copied = session.get(MediaFile, copy_id)
        assert copied.project_id == int(project["id"]) and copied.scope_user_id is None
        assert copied.created_by == int(owner_user["id"]) and copied.published_at is not None
        _assert_png(resource_app[0].state.storage, settings, copied, content)
        _assert_png(
            resource_app[0].state.storage,
            settings,
            session.get(MediaFile, baseline["media_id"]),
            content,
        )
        uploads = session.scalars(
            select(CanvasResourceUpload).where(
                CanvasResourceUpload.canvas_id == int(project["primary_canvas_id"])
            )
        ).all()
        assert len(uploads) == 1
        upload = uploads[0]
        assert upload.mode == "copy" and upload.status == "ready" and upload.media_id == copy_id
        assert upload.user_id == int(owner_user["id"])
        provenance = session.scalar(
            select(CanvasResourceCopySource).where(CanvasResourceCopySource.upload_id == upload.id)
        )
        assert provenance.original_resource_id == int(candidate["media_id"])
        assert provenance.released_at and provenance.source_media_id is None
        nodes = session.scalars(
            select(CanvasNode).where(CanvasNode.canvas_id == int(project["primary_canvas_id"]))
        ).all()
        assert {row.node_key for row in nodes} == set(result["node_ids"])
        assert len(nodes) == 3
        assert "owner-private" not in json.dumps([row.content_json for row in nodes])
        references = session.scalars(
            select(CanvasMediaReference).where(
                CanvasMediaReference.canvas_id == int(project["primary_canvas_id"])
            )
        ).all()
        assert len(references) == 3 and all(
            row.media_id == copy_id and row.owner_kind == "node" for row in references
        )
        states = session.scalars(
            select(CanvasNodeUserState).where(
                CanvasNodeUserState.canvas_id == int(project["primary_canvas_id"])
            )
        ).all()
        author_states = [row for row in states if row.user_id == int(owner_user["id"])]
        assert "owner-private" in json.dumps([row.draft_json for row in author_states])
        assert all(
            "owner-private" not in json.dumps(row.draft_json)
            for row in states
            if row.user_id != int(owner_user["id"])
        )
        for model in (AsyncTask, AIGenerationRecord):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert database_errors == []
