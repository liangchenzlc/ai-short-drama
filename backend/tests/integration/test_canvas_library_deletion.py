"""Source delete/trash semantics against isolated MySQL and real MinIO bytes."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from short_drama.core.exceptions import NotFound, StorageUnavailable, WorkflowError
from short_drama.dao.canvas_library_dao import CanvasLibraryDAO
from short_drama.domain import (
    Asset,
    CanvasBinaryResource,
    CanvasResourceDeletion,
    CanvasResourceUpload,
    MediaFile,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas_resource_cleanup import cleanup_canvas_resources
from short_drama.utils.snowflake import next_id
from tests.integration.test_canvas_library import ASSETS, text_asset
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import ROOT, canvas, media_asset_data, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real isolated canvas MinIO verification",
    ),
]


def register(client, key, resource, *, status="confirmed"):
    result = client.put(
        ASSETS + "/" + key,
        json={
            "asset": {
                "id": key,
                "kind": "model" if resource["kind"] == "file" else resource["kind"],
                "title": "删除验证",
                "status": status,
                "data": media_asset_data(resource),
            }
        },
    )
    assert result.status_code == 200, result.text


def cleanup(app, **options):
    return cleanup_canvas_resources(app[1], app[0].state.storage, app[2], **options)


def commit(client, path, document, key):
    version = client.get(path + "/my-document").json()["row_version"]
    result = client.post(
        path + "/commits",
        headers={"Idempotency-Key": key},
        json={
            "expected_row_version": version,
            "source_document": document,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def image_node(resource, **metadata):
    return {
        "id": "image",
        "type": "image",
        "title": "作品",
        "position": {"x": 0, "y": 0},
        "width": 320,
        "height": 200,
        "metadata": {"storageKey": "resource:" + resource["id"], **metadata},
    }


def test_delete_contract_and_restore_wins_over_stale_clear_trash(resource_app, monkeypatch):
    owner, _ = account(resource_app, "delete_trash")
    other, _ = account(resource_app, "delete_other")
    text_asset(owner, "same", status="archived")
    text_asset(other, "same")
    assert owner.delete(ASSETS + "/same", params={"expectedStatus": "confirmed"}).status_code == 422
    assert other.delete(ASSETS + "/missing").status_code == 404
    entered, restored = Event(), Event()
    original = CanvasLibraryDAO.asset

    def synchronized(dao, key, **options):
        result = original(dao, key, **options)
        if key == "same" and options.get("lock") and result.status == "archived":
            entered.set()
            assert restored.wait(10)
        return result

    # Pause the real restore while it holds the library serialization lock. The
    # pending clear-trash request must inspect the committed restored status.
    monkeypatch.setattr(CanvasLibraryDAO, "asset", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        restoration = pool.submit(text_asset, owner, "same", status="confirmed")
        assert entered.wait(10)
        deletion = pool.submit(
            owner.delete, ASSETS + "/same", params={"expectedStatus": "archived"}
        )
        restored.set()
        restoration.result()
        rejected = deletion.result()
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_asset_trash_conflict"
    assert owner.get(ASSETS + "/same").json()["asset"]["status"] == "confirmed"
    assert owner.delete(ASSETS + "/same").json() == {"id": "same"}
    assert owner.delete(ASSETS + "/same").status_code == 404
    assert other.get(ASSETS + "/same").status_code == 200


@pytest.mark.parametrize("lost_response", [False, True])
def test_delete_is_atomic_and_durable_cleanup_retries_unknown_minio_result(
    resource_app,
    monkeypatch,
    lost_response,
    database_errors,
):
    owner, user = account(resource_app, "delete_retry")
    other, other_user = account(resource_app, "delete_retry_other")
    body = png()
    resource = upload(owner, body).json()["resource"]
    register(owner, "delete", resource, status="archived")
    assert other.delete(ASSETS + "/delete").status_code == 404
    identifier = int(resource["id"])
    storage, settings = resource_app[0].state.storage, resource_app[2]
    key = f"canvas/resources/{identifier}"
    deleted = owner.delete(ASSETS + "/delete", params={"expectedStatus": "archived"})
    assert deleted.status_code == 200, (deleted.text, database_errors)
    assert storage.stat(settings.minio_image_bucket, key).size == len(body)
    assert owner.get(f"{ROOT}/{identifier}/file").status_code == 404
    with resource_app[1]() as session:
        assert session.get(MediaFile, identifier) is None
        assert session.scalar(select(CanvasResourceUpload.id)) is None
        job = session.scalar(select(CanvasResourceDeletion))
        job_id, upload_id = job.id, job.upload_id
        assert job.status == "pending" and job.attempts == 0
    # A scoped session can see only its own immutable deletion receipt.
    with resource_app[1]() as session:
        session.info["actor"] = SimpleNamespace(user_id=int(other_user["id"]))
        assert session.scalar(select(CanvasResourceDeletion)) is None
    with resource_app[1]() as session:
        session.info["actor"] = SimpleNamespace(user_id=int(user["id"]))
        job = session.get(CanvasResourceDeletion, job_id)
        job.status = "completed"
        with pytest.raises(WorkflowError, match="immutable"):
            session.flush()
        session.rollback()
    assert cleanup(resource_app)["examined"] == 1
    assert storage.stat(settings.minio_image_bucket, key).size == len(body)
    remove = storage.remove

    def unavailable(bucket, object_name, **options):
        if lost_response:
            remove(bucket, object_name, **options)
        raise StorageUnavailable("injected object-store response loss")

    monkeypatch.setattr(storage, "remove", unavailable)
    assert cleanup(resource_app, apply=True)["failed"] == 1
    with resource_app[1].begin() as session:
        job = session.get(CanvasResourceDeletion, job_id)
        assert job.status == "pending" and job.attempts == 1 and job.error_code
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
    monkeypatch.setattr(storage, "remove", remove)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: cleanup(resource_app, apply=True), range(2)))
    assert sum(result["completed"] for result in outcomes) == 1
    with pytest.raises(NotFound):
        storage.stat(settings.minio_image_bucket, key)
    assert cleanup(resource_app, apply=True)["examined"] == 0
    replay = upload(owner, body)
    assert replay.status_code == 410 and replay.json()["error"]["code"] == "canvas_resource_deleted"
    assert upload(owner, png("blue")).status_code == 409
    assert owner.post(f"{ROOT}/uploads/{upload_id}/complete").status_code == 410
    assert other.post(f"{ROOT}/uploads/{upload_id}/complete").status_code == 404
    replacement = upload(owner, body, key="new-explicit-upload")
    assert replacement.status_code == 200 and replacement.json()["resource"]["id"] != resource["id"]
    assert database_errors == []


def test_other_library_asset_retains_resource_until_last_owner_deletes(resource_app):
    owner, _ = account(resource_app, "delete_shared")
    resource = upload(owner, png()).json()["resource"]
    register(owner, "first", resource)
    register(owner, "second", resource)
    deleted = owner.delete(ASSETS + "/first")
    assert deleted.status_code == 200, deleted.text
    assert owner.get(f"{ROOT}/{resource['id']}/file").content == png()
    assert cleanup(resource_app, apply=True)["examined"] == 0
    assert owner.delete(ASSETS + "/second").status_code == 200
    assert cleanup(resource_app, apply=True)["completed"] == 1


@pytest.mark.parametrize("legacy_numeric", [False, True])
def test_standard_reference_image_json_keeps_canvas_uploaded_bytes(resource_app, legacy_numeric):
    owner, _ = account(resource_app, "delete_standard_reference")
    resource = upload(owner, png()).json()["resource"]
    register(owner, "standard-reference", resource)
    created = owner.post(
        "/api/v1/libraries/global/assets",
        headers={"Idempotency-Key": "standard-reference"},
        json={"kind": "character", "name": "标准素材"},
    )
    assert created.status_code == 201, created.text
    asset_id = int(created.json()["id"])
    with resource_app[1].begin() as session:
        asset = session.get(Asset, asset_id)
        asset.reference_media_ids = [int(resource["id"]) if legacy_numeric else resource["id"]]
    rejected = owner.delete(ASSETS + "/standard-reference")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_asset_in_use"
    assert owner.get(f"{ROOT}/{resource['id']}/file").content == png()
    with resource_app[1].begin() as session:
        session.get(Asset, asset_id).reference_media_ids = []
    assert owner.delete(ASSETS + "/standard-reference").status_code == 200
    assert owner.get(f"/api/v1/assets/{asset_id}").status_code == 200


def test_live_and_member_private_references_protect_without_disclosing_member_data(resource_app):
    owner, _ = account(resource_app, "delete_author")
    member, member_user = account(resource_app, "delete_member")
    project, path, document = canvas(owner, "delete-references")
    join(resource_app, owner, member, project["id"], member_user["id"])
    resource = upload(owner, png(), source=document["id"]).json()["resource"]
    register(owner, "referenced", resource)
    document["nodes"] = [image_node(resource)]
    commit(owner, path, document, "attach")
    result = owner.delete(ASSETS + "/referenced")
    assert result.status_code == 409 and result.json()["error"]["code"] == "canvas_asset_in_use"
    member_document = member.get(path + "/my-document").json()["source_document"]
    member_document["nodes"][0]["metadata"] = {
        "prompt": "不能泄露成员提示词",
        "referenceImages": ["resource:" + resource["id"]],
    }
    commit(member, path, member_document, "member-private-reference")
    rejected = owner.delete(ASSETS + "/referenced")
    assert rejected.status_code == 409, rejected.text
    assert "不能泄露" not in rejected.text and member_user["id"] not in rejected.text
    assert member.get(f"{ROOT}/{resource['id']}/file").content == png()
    member_document["nodes"][0]["metadata"] = {"content": "解除私人引用"}
    commit(member, path, member_document, "remove-member-reference")
    deleted = owner.delete(ASSETS + "/referenced")
    assert deleted.status_code == 200, deleted.text


@pytest.mark.parametrize("kind", ["image", "file"])
def test_history_keeps_media_and_binary_resource_after_live_binding_removed(resource_app, kind):
    owner, _ = account(resource_app, "delete_history")
    _, path, document = canvas(owner, "history")
    resource = upload(
        owner,
        png() if kind == "image" else b"glTF",
        source=document["id"],
        kind=kind,
        name="image.png" if kind == "image" else "model.glb",
        mime="image/png" if kind == "image" else "model/gltf-binary",
    ).json()["resource"]
    register(owner, "history", resource)
    document["directorScenes"] = [{"id": "scene", "model": "resource:" + resource["id"]}]
    saved = commit(owner, path, document, "attach-history")
    empty_revision = owner.get(path + "/revisions").json()["items"][0]
    restored = owner.post(
        path + f"/revisions/{empty_revision['id']}/restore",
        headers={"Idempotency-Key": "restore-empty"},
        json={"expected_row_version": saved["row_version"]},
    )
    assert restored.status_code == 200, restored.text
    rejected = owner.delete(ASSETS + "/history")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_asset_history_referenced"
    assert cleanup(resource_app, apply=True)["examined"] == 0
    latest = owner.get(path + "/revisions").json()["items"][0]
    recovered = owner.post(
        path + f"/revisions/{latest['id']}/restore",
        headers={"Idempotency-Key": "restore-resource"},
        json={"expected_row_version": restored.json()["row_version"]},
    )
    assert recovered.status_code == 200, recovered.text
    assert owner.get(f"{ROOT}/{resource['id']}/file").content == (
        png() if kind == "image" else b"glTF"
    )


def test_delete_uses_current_reference_after_earlier_repeatable_read_snapshot(
    resource_app, monkeypatch, database_errors
):
    owner, _ = account(resource_app, "delete_mvcc")
    _, path, document = canvas(owner, "mvcc")
    resource = upload(owner, png(), source=document["id"]).json()["resource"]
    register(owner, "late-binding", resource)
    entered, committed = Event(), Event()
    original = CanvasLibraryDAO.asset

    def synchronized(dao, key, **options):
        result = original(dao, key, **options)
        if key == "late-binding" and not options.get("lock"):
            entered.set()
            assert committed.wait(10)
        return result

    monkeypatch.setattr(CanvasLibraryDAO, "asset", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(owner.delete, ASSETS + "/late-binding")
        assert entered.wait(10)
        document["nodes"] = [image_node(resource)]
        try:
            commit(owner, path, document, "late-canvas-commit")
        finally:
            committed.set()
        rejected = pending.result()
    assert rejected.status_code == 409, (rejected.text, database_errors)
    assert rejected.json()["error"]["code"] == "canvas_asset_in_use"
    assert database_errors == []


def test_sql_failure_rolls_back_asset_receipt_and_resource_together(resource_app):
    owner, _ = account(resource_app, "delete_rollback")
    resource = upload(owner, png()).json()["resource"]
    register(owner, "rollback", resource)
    engine = resource_app[1].kw["bind"]

    def reject_final_delete(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("DELETE FROM media_files"):
            raise RuntimeError("injected final SQL failure")

    event.listen(engine, "before_cursor_execute", reject_final_delete)
    try:
        with pytest.raises(RuntimeError, match="injected final SQL failure"):
            owner.delete(ASSETS + "/rollback")
    finally:
        event.remove(engine, "before_cursor_execute", reject_final_delete)
    assert owner.get(ASSETS + "/rollback").status_code == 200
    assert owner.get(f"{ROOT}/{resource['id']}/file").content == png()
    with resource_app[1]() as session:
        assert session.scalar(select(CanvasResourceDeletion)) is None
        assert session.scalar(select(CanvasResourceUpload)).media_id == int(resource["id"])


def test_worker_preserves_other_accounts_same_physical_object(resource_app):
    owner, _ = account(resource_app, "delete_locator")
    _, other_user = account(resource_app, "delete_locator_other")
    resource = upload(owner, png()).json()["resource"]
    register(owner, "locator", resource)
    with resource_app[1].begin() as session:
        original = session.get(MediaFile, int(resource["id"]))
        session.add(
            CanvasBinaryResource(
                id=next_id(),
                scope_user_id=int(other_user["id"]),
                project_id=None,
                published_at=None,
                resource_kind="file",
                mime_type="image/png",
                storage_locator=original.storage_locator,
                original_name="shared-bytes.png",
                byte_size=original.byte_size,
                checksum_sha256=original.checksum_sha256,
                created_at=utcnow(),
                updated_at=utcnow(),
                created_by=int(other_user["id"]),
                updated_by=int(other_user["id"]),
            )
        )
    assert owner.delete(ASSETS + "/locator").status_code == 200
    assert cleanup(resource_app, apply=True)["retained"] == 1
    assert resource_app[0].state.storage.stat(
        resource_app[2].minio_image_bucket, f"canvas/resources/{resource['id']}"
    ).size == len(png())


def test_chunked_file_deletion_cleans_staging_and_tombstones_completion(resource_app):
    owner, _ = account(resource_app, "delete_chunks")
    body = b"glTF" + bytes(32)
    request = {"fileName": "model.glb", "kind": "file", "size": len(body)}
    headers = {"X-Idempotency-Key": "chunk-delete"}
    started = owner.post(ROOT + "/uploads", json=request, headers=headers)
    assert started.status_code == 200, started.text
    upload_id = started.json()["uploadId"]
    assert owner.put(f"{ROOT}/uploads/{upload_id}/chunks/0", content=body).status_code == 200
    resource = owner.post(f"{ROOT}/uploads/{upload_id}/complete").json()["resource"]
    register(owner, "chunked", resource)
    # Recreate the original staging object to model a previous failed cleanup.
    storage, bucket = resource_app[0].state.storage, resource_app[2].minio_video_bucket
    chunk_key = f"canvas/uploads/{upload_id}/0"
    storage.put(bucket, chunk_key, BytesIO(body), len(body), "application/octet-stream")
    assert owner.delete(ASSETS + "/chunked").status_code == 200
    assert cleanup(resource_app, apply=True)["completed"] == 1
    for key in (chunk_key, f"canvas/resources/{resource['id']}"):
        with pytest.raises(NotFound):
            storage.stat(bucket, key)
    assert owner.post(ROOT + "/uploads", json=request, headers=headers).status_code == 410
    assert owner.post(f"{ROOT}/uploads/{upload_id}/complete").status_code == 410
    assert owner.put(f"{ROOT}/uploads/{upload_id}/chunks/0", content=body).status_code == 410
