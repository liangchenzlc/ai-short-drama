"""Real authenticated HTTP + isolated MySQL + task-owned MinIO buckets."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.core.exceptions import NotFound, StorageUnavailable
from short_drama.dao.canvas_library_dao import CanvasLibraryDAO
from short_drama.domain import CanvasBinaryResource, CanvasResourceUpload, MediaFile
from short_drama.domain.canvas_library import CanvasLibraryAsset, CanvasLibraryAssetReference
from short_drama.domain.canvas_resource import CanvasBinaryReference
from short_drama.service.base import utcnow
from short_drama.service.canvas_resource_cleanup import cleanup_canvas_resources
from short_drama.service.canvas_resource_service import CHUNK_SIZE, CanvasResourceService
from short_drama.service.canvas_service import CanvasService
from short_drama.service.canvas_upload_cleanup import cleanup_canvas_uploads
from short_drama.service.video_render import executable
from short_drama.storage.minio import MinioStorage
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real isolated canvas MinIO verification",
    ),
]
ROOT = "/api/v1/canvas-runtime/resources"


@pytest.fixture
def resource_app(identity_app):
    app, factory, settings = identity_app
    prefix = "canvas-test-" + uuid4().hex
    isolated = settings.model_copy(
        update={
            "minio_image_bucket": prefix + "-image",
            "minio_video_bucket": prefix + "-video",
            "minio_audio_bucket": prefix + "-audio",
        }
    )
    storage = MinioStorage(isolated)
    created = []
    try:
        for bucket in (
            isolated.minio_image_bucket,
            isolated.minio_video_bucket,
            isolated.minio_audio_bucket,
        ):
            assert bucket.startswith(prefix)
            storage.client.make_bucket(bucket)
            created.append(bucket)
        app.state.settings, app.state.storage = isolated, storage
        yield app, factory, isolated
    finally:
        for bucket in created:
            assert bucket.startswith(prefix)
            for item in storage.client.list_objects(bucket, recursive=True):
                storage.client.remove_object(bucket, item.object_name)
            storage.client.remove_bucket(bucket)
        storage.close()


def png(color="red"):
    content = BytesIO()
    Image.new("RGB", (37, 19), color).save(content, "PNG")
    return content.getvalue()


def canvas(client, key):
    result = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": key},
        json={
            "name": "资源验证",
            "aspect": "16:9",
            "workspace_mode": "infinite_canvas",
        },
    )
    assert result.status_code == 201, result.text
    project = result.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    doc = client.get(path + "/my-document").json()["source_document"]
    return project, path, doc


def upload(
    client,
    content,
    *,
    key="image-upload",
    source=None,
    kind="image",
    name="image.png",
    mime="image/png",
):
    headers = {"X-Idempotency-Key": key}
    if source:
        headers["X-Canvas-Key"] = source
    return client.post(
        ROOT,
        data={"kind": kind, "width": "999", "height": "999"},
        files={"file": (name, content, mime)},
        headers=headers,
    )


def media_asset_data(resource):
    """Build a source-valid asset from metadata measured by the real resource service."""
    data = {
        "storageKey": "resource:" + resource["id"],
        "bytes": resource["size"],
        "mimeType": resource["mimeType"],
    }
    if resource["kind"] == "image":
        data.update(dataUrl="", width=resource["width"], height=resource["height"])
    else:
        data["url"] = ""
        if resource["kind"] == "video":
            data.update(width=resource["width"], height=resource["height"])
        if resource["kind"] in {"video", "audio"}:
            data["durationMs"] = resource["durationMs"]
        elif resource["kind"] == "file":
            data["fileName"] = "model.glb"
    return data


def test_upload_idempotency_actual_metadata_range_and_source_publication(resource_app):
    owner, user = account(resource_app, "canvas_media_owner")
    member, other = account(resource_app, "canvas_media_member")
    project, path, document = canvas(owner, "media-project")
    join(resource_app, owner, member, project["id"], other["id"])
    content = png()
    response = upload(owner, content, source=document["id"])
    assert response.status_code == 200, response.text
    resource = response.json()["resource"]
    identifier = resource["id"]
    assert isinstance(identifier, str) and int(identifier) > 2**53
    assert resource["width"] == 37 and resource["height"] == 19
    assert resource["objectKey"] == resource["publicUrl"] == ""
    assert resource["etag"] == hashlib.sha256(content).hexdigest()
    assert upload(owner, content, source=document["id"]).json() == response.json()
    conflict = upload(owner, png("blue"), source=document["id"])
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "canvas_upload_conflict"
    assert member.get(f"{ROOT}/{identifier}").status_code == 404
    assert member.get(f"{ROOT}/{identifier}/file").status_code == 404
    assert owner.get(f"{ROOT}/{identifier}/file").content == content
    partial = owner.get(f"{ROOT}/{identifier}/file?proxy=1", headers={"Range": "bytes=4-15"})
    assert partial.status_code == 206 and partial.content == content[4:16]
    assert partial.headers["Content-Range"] == f"bytes 4-15/{len(content)}"
    assert (
        owner.get(f"{ROOT}/{identifier}/file", headers={"Range": "bytes=-5"}).content
        == content[-5:]
    )
    assert (
        owner.get(f"{ROOT}/{identifier}/file", headers={"Range": "bytes=99999-"}).status_code == 416
    )
    assert owner.head(f"{ROOT}/{identifier}/file").headers["Content-Length"] == str(len(content))
    # Merely saving a private reference does not publish it.
    document["nodes"] = [
        {
            "id": "media",
            "type": "image",
            "title": "图片",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"prompt": "private", "referenceImages": [f"resource:{identifier}"]},
        }
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "private-ref"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    assert member.get(f"{ROOT}/{identifier}").status_code == 404
    document["nodes"][0]["metadata"]["storageKey"] = f"resource:{identifier}"
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "attach-output"},
        json={"expected_row_version": saved.json()["row_version"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    assert member.get(f"{ROOT}/{identifier}/file").content == content
    with resource_app[1]() as session:
        media = session.get(MediaFile, int(identifier))
        assert media.published_at and media.project_id == int(project["id"])
        assert session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.media_id == media.id)
        ).user_id == int(user["id"])


def test_upload_preparation_overlaps_autosave_without_parent_lock_deadlock(
    resource_app, database_errors, monkeypatch
):
    owner, _ = account(resource_app, "canvas_upload_autosave")
    _, path, document = canvas(owner, "upload-autosave")
    document["nodes"] = [
        {
            "id": "during-upload",
            "type": "text",
            "title": "上传时编辑",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 200,
            "metadata": {"content": "仍可保存"},
        }
    ]
    upload_ready, graph_ready = Event(), Event()
    scope = CanvasResourceService._scope
    snapshot = CanvasService._snapshot

    def synchronized_scope(service, key):
        upload_ready.set()
        assert graph_ready.wait(5), "autosave did not acquire the project lock"
        return scope(service, key)

    def synchronized_snapshot(service, row, value, *, reason):
        graph_ready.set()
        return snapshot(service, row, value, reason=reason)

    monkeypatch.setattr(CanvasResourceService, "_scope", synchronized_scope)
    monkeypatch.setattr(CanvasService, "_snapshot", synchronized_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        preparing = pool.submit(
            owner.post,
            ROOT + "/uploads",
            headers={"X-Idempotency-Key": "parallel-upload", "X-Canvas-Key": document["id"]},
            json={"fileName": "file.glb", "kind": "file", "size": 32},
        )
        assert upload_ready.wait(5)
        saving = pool.submit(
            owner.post,
            path + "/commits",
            headers={"Idempotency-Key": "upload-autosave-commit"},
            json={"expected_row_version": document["revision"], "source_document": document},
        )
        responses = [preparing.result(timeout=10), saving.result(timeout=10)]
    assert [response.status_code for response in responses] == [200, 200], (
        [response.text for response in responses],
        database_errors,
    )
    assert database_errors == []


def test_chunk_restart_duplicate_completion_binary_history_and_foreign_account(resource_app):
    owner, _ = account(resource_app, "canvas_chunk_owner")
    stranger, _ = account(resource_app, "canvas_chunk_stranger")
    _, path, document = canvas(owner, "chunk-project")
    body = b"glTF" + bytes(CHUNK_SIZE + 23)
    request = {"fileName": "model.glb", "kind": "file", "size": len(body)}
    headers = {"X-Idempotency-Key": "chunk-key", "X-Canvas-Key": document["id"]}
    started = owner.post(ROOT + "/uploads", json=request, headers=headers)
    assert started.status_code == 200, started.text
    upload_id = started.json()["uploadId"]
    assert owner.post(ROOT + "/uploads", json=request, headers=headers).json() == started.json()
    assert started.json()["chunkSize"] == CHUNK_SIZE and started.json()["chunkCount"] == 2
    assert stranger.post(f"{ROOT}/uploads/{upload_id}/complete").status_code == 404
    assert owner.post(f"{ROOT}/uploads/{upload_id}/complete").status_code == 409
    for index, chunk in enumerate((body[:CHUNK_SIZE], body[CHUNK_SIZE:])):
        chunk_path = f"{ROOT}/uploads/{upload_id}/chunks/{index}"
        result = owner.put(
            chunk_path, content=chunk, headers={"Content-Type": "application/octet-stream"}
        )
        assert result.status_code == 200, result.text
        assert owner.put(chunk_path, content=chunk).status_code == 200
        assert owner.put(chunk_path, content=b"x").status_code == 422
    changed = owner.put(f"{ROOT}/uploads/{upload_id}/chunks/1", content=b"x" * 27)
    assert changed.status_code == 409, changed.text
    complete = owner.post(f"{ROOT}/uploads/{upload_id}/complete")
    assert complete.status_code == 200, complete.text
    assert owner.post(f"{ROOT}/uploads/{upload_id}/complete").json() == complete.json()
    resource = complete.json()["resource"]
    identifier = resource["id"]
    assert resource["kind"] == "file" and resource["mimeType"] == "model/gltf-binary"
    assert owner.get(f"{ROOT}/{identifier}/file").content == body
    document["directorScenes"] = [{"id": "scene", "model": f"resource:{identifier}"}]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "binary-attach"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    with resource_app[1]() as session:
        resource = session.get(CanvasBinaryResource, int(identifier))
        assert resource.published_at is not None
        assert session.get(MediaFile, int(identifier)) is None
        assert session.scalar(
            select(CanvasBinaryReference).where(CanvasBinaryReference.binary_id == resource.id)
        )
    revision = owner.get(path + "/revisions").json()["items"][0]
    restored = owner.post(
        path + f"/revisions/{revision['id']}/restore",
        headers={"Idempotency-Key": "restore-before-binary"},
        json={"expected_row_version": saved.json()["row_version"]},
    )
    assert restored.status_code == 200, restored.text
    with resource_app[1]() as session:
        assert session.scalar(
            select(CanvasBinaryReference.id).where(
                CanvasBinaryReference.binary_id == int(identifier),
                CanvasBinaryReference.revision_id.is_not(None),
            )
        )
    cleanup = cleanup_canvas_uploads(
        resource_app[1], resource_app[0].state.storage, resource_app[2], apply=True
    )
    assert cleanup["failed"] == 0
    assert owner.get(f"{ROOT}/{identifier}/file").content == body


def test_audio_and_video_are_probed_and_streamed_from_real_storage(resource_app, tmp_path):
    owner, _ = account(resource_app, "canvas_av_owner")
    audio = BytesIO()
    with wave.open(audio, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(bytes(16000))
    result = upload(
        owner, audio.getvalue(), key="audio", kind="audio", name="sound.wav", mime="audio/wav"
    )
    assert result.status_code == 200, result.text
    assert result.json()["resource"]["durationMs"] == 1000
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=160x90:rate=24",
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
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result = upload(
        owner, video.read_bytes(), key="video", kind="video", name="video.mp4", mime="video/mp4"
    )
    assert result.status_code == 200, result.text
    resource = result.json()["resource"]
    assert (resource["width"], resource["height"], resource["durationMs"]) == (160, 90, 1000)
    assert owner.get(f"{ROOT}/{resource['id']}/file?variant=playback").content == video.read_bytes()


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
    reason="Enable explicit real browser verification",
)
def test_source_upload_operation_persists_and_refreshes_in_real_browser(
    resource_app, database_errors
):
    from tests.integration.test_canvas_browser import isolated_api, stop_browser

    client, _ = account(resource_app, "canvas_resource_browser")
    client.post("/api/v1/auth/logout")
    other, _ = account(resource_app, "canvas_resource_other")
    other.post("/api/v1/auth/logout")
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4187"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "username": "canvas_resource_browser",
            "otherUsername": "canvas_resource_other",
            "password": PASSWORD,
            "image": base64.b64encode(png()).decode(),
        }
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-resource-python.mjs"],
            cwd=Path(__file__).resolve().parents[3] / "frontend" / "canvas",
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
            pytest.fail(f"Canvas resource browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Canvas resource browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        result = json.loads(output)
        assert result["image_uploaded"] and result["image_saved"] and result["image_restored"]
        assert result["downloaded_from_storage"]
        assert result["library_classified"] and result["library_unclassified_after_delete"]
        assert (
            result["library_restored"]
            and result["library_deleted"]
            and result["library_delete_blocked"]
        )
        assert result["copy_identity_restored"] and result["copy_source_matchers_unchanged"]
        assert result["copy_edit_saved_and_refreshed"]
        assert result["source_picker_normalized"]
        assert result["normalization_preserves_live_drag_and_delete"]
        assert result["normalization_preserves_undo_redo"]
        assert result["source_picker_fresh_context_restored"]
        assert result["source_picker_original_asset_preserved"]
        assert result["normalization_draft_provenance_restored"]
        assert result["normalization_retry_preserves_key_and_body"]
        assert result["source_project_copy_atomic"]
        assert result["source_project_rename_saved"]
        assert result["source_same_project_copy_reuses_media"]
        assert result["source_multi_canvas_copy_group_preserved"]
        assert result["source_project_copy_fresh_context_restored"]
        assert result["creation_recovery_before_send"]
        assert result["creation_recovery_after_commit"]
        assert result["creation_group_recovery"]
        assert result["creation_recovery_keeps_later_edits"]
        assert result["creation_missing_get_never_recreates"]
        assert result["creation_group_partial_storage_recovered"]
        assert result["creation_group_missing_cache_recovered"]
        assert result["creation_navigation_preserved"]
        assert result["creation_account_switch_isolated"]
        assert result["creation_readonly_copy_persisted"]
        cleaned = cleanup_canvas_resources(
            resource_app[1], resource_app[0].state.storage, settings, apply=True
        )
        assert cleaned["completed"] == 1 and cleaned["failed"] == 0
        with pytest.raises(NotFound):
            resource_app[0].state.storage.stat(
                settings.minio_image_bucket, f"canvas/resources/{result['deleted_resource_id']}"
            )


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
    reason="Enable explicit real browser verification",
)
def test_source_tray_and_clipboard_operations_in_real_browser(resource_app, database_errors):
    from tests.integration.test_canvas_browser import isolated_api, stop_browser

    client, _ = account(resource_app, "canvas_tray_browser")
    client.post("/api/v1/auth/logout")
    other, _ = account(resource_app, "canvas_tray_other")
    other.post("/api/v1/auth/logout")
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4188"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "username": "canvas_tray_browser",
            "otherUsername": "canvas_tray_other",
            "password": PASSWORD,
            "image": base64.b64encode(png()).decode(),
        }
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-tray-clipboard-python.mjs"],
            cwd=Path(__file__).resolve().parents[3] / "frontend" / "canvas",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=150)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Canvas tray/clipboard browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Canvas tray/clipboard browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        result = json.loads(output)
        assert result["tray_click_persisted"] and result["tray_drag_persisted"]
        assert result["tray_failed_read_retried"] and result["source_connection_persisted"]
        assert result["clipboard_same_project"] and result["clipboard_cross_project"]
        assert result["original_asset_preserved"]
        assert result["clipboard_account_isolated"]
        assert result["tray_all_pages_and_deletion_refreshed"]
        assert result["canonical_media_requests_clean"]
        assert result["page_errors"] == []


def test_library_registration_is_private_and_numeric_keys_do_not_enter_standard_assets(
    resource_app,
):
    owner, _ = account(resource_app, "canvas_library_owner")
    member, other = account(resource_app, "canvas_library_member")
    project, _, document = canvas(owner, "library-project")
    join(resource_app, owner, member, project["id"], other["id"])
    resource = upload(owner, png(), source=document["id"]).json()["resource"]
    asset = {
        "id": "123",
        "kind": "image",
        "title": "私有提示标题",
        "coverUrl": "",
        "tags": [],
        "metadata": {"canvasId": document["id"], "prompt": "私人指令"},
        "data": media_asset_data(resource),
    }
    path = "/api/v1/canvas-runtime/assets"
    saved = owner.put(path + "/123", json={"asset": asset})
    assert saved.status_code == 200, saved.text
    assert owner.put(path + "/123", json={"asset": asset}).json() == saved.json()
    assert owner.get(path + "/123").json()["asset"]["metadata"]["prompt"] == "私人指令"
    assert member.get(path + "/123").status_code == 404
    assert member.post(path + "/batch", json={"ids": ["123"]}).json() == {"assets": []}
    assert member.get(path).json() == {"assets": []}
    page = owner.get(path, params={"page": "1", "pageSize": "1", "status": "active"})
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1 and page.json()["assets"][0]["id"] == "123"
    assert page.json()["categoryCounts"] == {"material": 1}
    assert page.json()["kindCounts"] == {"image": 1}
    assert page.json()["projectCounts"] == {"未关联项目": 1}
    assert page.json()["recentTotal"] == 1 and page.json()["generatedTotal"] == 0
    assert member.get(path, params={"page": "1"}).json()["total"] == 0
    asset["metadata"].update(favorite=True, generationEffectKey="", projectName="测试分组")
    updated = owner.put(path + "/123", json={"asset": asset})
    assert updated.status_code == 200, updated.text
    page = owner.get(
        path,
        params={"page": "1", "favorite": "1", "generated": "1", "project": "测试分组", "q": "私有"},
    )
    assert page.status_code == 200, page.text
    assert (
        page.json()["total"] == page.json()["favoriteTotal"] == page.json()["generatedTotal"] == 1
    )


def test_library_rejects_source_invalid_records_without_losing_saved_content(resource_app):
    owner, user = account(resource_app, "canvas_library_contract")
    resource = upload(owner, png()).json()["resource"]
    path = "/api/v1/canvas-runtime/assets/strict-image"
    asset = {
        "id": "strict-image",
        "kind": "image",
        "title": "完整图片",
        "data": {**media_asset_data(resource), "mimeType": "  IMAGE/PNG  "},
        "source": "原版素材库",
        "portraitCertified": False,
    }
    saved = owner.put(path, json={"asset": asset})
    assert saved.status_code == 200, saved.text
    original = owner.get(path).json()["asset"]
    assert original["data"] == media_asset_data(resource)
    assert original["portraitCertified"] is False
    assert original["createdAt"] and original["updatedAt"]
    invalid = []
    for field in ("dataUrl", "width", "height", "bytes", "mimeType"):
        changed = deepcopy(asset)
        changed["data"].pop(field)
        invalid.append(changed)
    for field, value in (("width", 0), ("bytes", True), ("mimeType", "audio/wav")):
        changed = deepcopy(asset)
        changed["data"][field] = value
        invalid.append(changed)
    invalid.append({**asset, "portraitCertified": "false"})
    invalid.append({**asset, "source": 123})
    for changed in invalid:
        response = owner.put(path, json={"asset": changed})
        assert response.status_code == 422, response.text
        assert owner.get(path).json()["asset"] == original
        changed["id"] = "rejected-new"
        response = owner.put(path.rsplit("/", 1)[0] + "/rejected-new", json={"asset": changed})
        assert response.status_code == 422, response.text
    with resource_app[1]() as session:
        rows = list(
            session.scalars(
                select(CanvasLibraryAsset).where(CanvasLibraryAsset.user_id == int(user["id"]))
            )
        )
        assert len(rows) == 1 and rows[0].source_key == "strict-image"
        assert list(
            session.scalars(
                select(CanvasLibraryAssetReference.media_id).where(
                    CanvasLibraryAssetReference.library_asset_id == rows[0].id
                )
            )
        ) == [int(resource["id"])]
    # Persisting the returned source document remains an idempotent normal save.
    assert owner.put(path, json={"asset": original}).json() == saved.json()
    assert owner.get(f"{ROOT}/{resource['id']}/file").content == png()


def test_library_replacement_guards_actual_node_and_timeline_bindings(resource_app, monkeypatch):
    owner, _ = account(resource_app, "canvas_library_replace")
    member, member_user = account(resource_app, "canvas_library_replace_member")
    project, canvas_path, document = canvas(owner, "library-replacement")
    join(resource_app, owner, member, project["id"], member_user["id"])
    resources = [
        upload(owner, png(color), key="replacement-" + color, source=document["id"]).json()[
            "resource"
        ]
        for color in ("red", "blue")
    ]
    first, second = [f"resource:{resource['id']}" for resource in resources]
    path = "/api/v1/canvas-runtime/assets/replace-image"
    asset = {
        "id": "replace-image",
        "kind": "image",
        "title": "可替换图片",
        "metadata": {"canvasId": document["id"]},
        "data": media_asset_data(resources[0]),
        "coverUrl": second,
    }

    def put(expected):
        response = owner.put(path, json={"asset": asset})
        assert response.status_code == expected, response.text
        return response

    def save(key):
        nonlocal document
        response = owner.post(
            canvas_path + "/commits",
            headers={"Idempotency-Key": key},
            json={"expected_row_version": document["revision"], "source_document": document},
        )
        assert response.status_code == 200, response.text
        document = owner.get(canvas_path + "/my-document").json()["source_document"]

    put(200)
    asset["coverUrl"] = ""
    put(200)  # Removing an unused cover must not be rejected as a resource replacement.
    document["nodes"] = [
        {
            "id": "bound-image",
            "type": "image",
            "title": "绑定图片",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 200,
            "metadata": {"assetId": asset["id"], "storageKey": first, "content": second},
        }
    ]
    save("replacement-node")
    asset["data"]["storageKey"] = second
    assert put(409).json()["error"]["code"] == "canvas_asset_resource_conflict"
    assert owner.get(path).json()["asset"]["data"]["storageKey"] == first
    # Source priority is storageKey before content, and a cover can preserve the binding.
    asset["coverUrl"] = f"/api/v1/canvas-runtime/resources/{resources[0]['id']}/file"
    put(200)
    asset["coverUrl"] = ""
    put(409)
    document["nodes"] = []
    document["timeline"] = {
        "clips": [
            {
                "id": "clip-a",
                "directMedia": {
                    "kind": "image",
                    "assetId": asset["id"],
                    "url": asset["coverUrl"] or f"/api/resources/{resources[0]['id']}/file",
                },
            }
        ]
    }
    save("replacement-timeline")
    for client, suffix in ((owner, ""), (member, "/my-document")):
        shared = client.get(canvas_path + suffix).json()["source_document"]
        assert "assetId" not in shared["timeline"]["clips"][0]["directMedia"]
    assert document["timeline"]["clips"][0]["directMedia"]["assetId"] == asset["id"]
    member_asset = {**asset, "data": media_asset_data(resources[1])}
    member_saved = member.put(path, json={"asset": member_asset})
    assert member_saved.status_code == 200, member_saved.text
    put(409)
    document["timeline"]["clips"] = []
    save("replacement-detached")
    put(200)
    with resource_app[1]() as session:
        row = session.scalar(
            select(CanvasLibraryAsset).where(
                CanvasLibraryAsset.user_id == int(project["owner_user_id"])
            )
        )
        assert set(
            session.scalars(
                select(CanvasLibraryAssetReference.media_id).where(
                    CanvasLibraryAssetReference.library_asset_id == row.id
                )
            )
        ) == {int(resources[1]["id"])}
    assert owner.get(f"{ROOT}/{resources[0]['id']}/file").status_code == 200
    # Earlier scope reads already opened a REPEATABLE READ snapshot. A graph
    # committed during that request must still be seen by the replacement guard.
    asset["coverUrl"] = first
    put(200)
    asset["coverUrl"] = ""
    document["nodes"] = [
        {
            "id": "late-binding",
            "type": "image",
            "title": "并发绑定",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 200,
            "metadata": {"assetId": asset["id"], "storageKey": first},
        }
    ]
    ready, graph_saved = Event(), Event()
    read_asset = CanvasLibraryDAO.asset

    def pause_replacement(dao, key, **options):
        if key == asset["id"] and options.get("lock"):
            ready.set()
            assert graph_saved.wait(10), "the concurrent graph did not commit"
        return read_asset(dao, key, **options)

    monkeypatch.setattr(CanvasLibraryDAO, "asset", pause_replacement)
    with ThreadPoolExecutor(max_workers=1) as pool:
        replacing = pool.submit(owner.put, path, json={"asset": asset})
        try:
            assert ready.wait(5)
            save("replacement-late-binding")
        finally:
            graph_saved.set()
        response = replacing.result(timeout=10)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "canvas_asset_resource_conflict"
    assert owner.get(path).json()["asset"]["coverUrl"] == first


def test_lost_storage_ack_reuses_reserved_identity_and_expired_staging_is_cleaned(
    resource_app, monkeypatch
):
    owner, _ = account(resource_app, "canvas_upload_recovery")
    storage = resource_app[0].state.storage
    original = storage.put
    fail = True

    def lose_first_ack(*args, **kwargs):
        nonlocal fail
        result = original(*args, **kwargs)
        if fail:
            fail = False
            raise StorageUnavailable("Injected lost storage acknowledgement")
        return result

    monkeypatch.setattr(storage, "put", lose_first_ack)
    response = upload(owner, png(), key="unknown-storage")
    assert response.status_code == 503, response.text
    with resource_app[1]() as session:
        pending = session.scalar(select(CanvasResourceUpload))
        reserved = pending.reserved_resource_id
        assert pending.status == "pending" and session.get(MediaFile, reserved) is None
    result = upload(owner, png(), key="unknown-storage")
    assert result.status_code == 200 and result.json()["resource"]["id"] == str(reserved)
    session = owner.post(
        ROOT + "/uploads",
        headers={"X-Idempotency-Key": "expired"},
        json={"fileName": "file.bin", "kind": "file", "size": 4},
    ).json()
    upload_id = int(session["uploadId"])
    assert owner.put(f"{ROOT}/uploads/{upload_id}/chunks/0", content=b"test").status_code == 200
    with resource_app[1].begin() as db:
        db.get(CanvasResourceUpload, upload_id).expires_at = utcnow() - timedelta(seconds=1)
    assert owner.post(f"{ROOT}/uploads/{upload_id}/complete").status_code == 410
    cleanup = cleanup_canvas_uploads(resource_app[1], storage, resource_app[2], apply=True)
    assert cleanup["failed"] == 0 and cleanup["remove_requests"] >= 1
    assert owner.get(f"{ROOT}/{reserved}/file").content == png()
    restarted = owner.post(
        ROOT + "/uploads",
        headers={"X-Idempotency-Key": "expired"},
        json={"fileName": "file.bin", "kind": "file", "size": 4},
    )
    assert restarted.status_code == 200 and restarted.json()["uploadId"] == str(upload_id)
    assert owner.put(f"{ROOT}/uploads/{upload_id}/chunks/0", content=b"test").status_code == 200
    assert owner.post(f"{ROOT}/uploads/{upload_id}/complete").status_code == 200
