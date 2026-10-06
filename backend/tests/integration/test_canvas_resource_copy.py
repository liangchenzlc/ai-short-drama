"""真实 MySQL/MinIO 的资源副本、权限、未知响应恢复及回收竞争。"""

import os
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from short_drama.core.exceptions import NotFound, StorageUnavailable, WorkflowError
from short_drama.domain import CanvasResourceCopySource, CanvasResourceUpload, MediaFile
from short_drama.service.base import utcnow
from short_drama.service.canvas_upload_cleanup import cleanup_canvas_uploads
from short_drama.service.video_render import executable
from tests.integration.test_canvas_library_deletion import ASSETS, cleanup, image_node, register
from tests.integration.test_canvas_resources import ROOT, canvas, png, upload
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


def copy_resource(client, resource, canvas_key, *, key="copy-resource"):
    return client.post(
        ROOT + "/copies",
        headers={"X-Idempotency-Key": key},
        json={"source_resource_id": resource["id"], "canvas_key": canvas_key},
    )


def pending_copy(app):
    with app[1]() as session:
        row = session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.mode == "copy")
        )
        source = session.scalar(select(CanvasResourceCopySource))
        return row, source


def test_copy_is_independent_private_until_attachment_and_original_can_be_deleted(resource_app):
    owner, user = account(resource_app, "copy_owner")
    member, member_user = account(resource_app, "copy_member")
    target, path, document = canvas(owner, "copy-target")
    join(resource_app, owner, member, target["id"], member_user["id"])
    original = upload(owner, png()).json()["resource"]
    register(owner, "original", original)
    response = copy_resource(owner, original, document["id"])
    assert response.status_code == 200, response.text
    copied = response.json()["resource"]
    assert copied["id"] != original["id"] and isinstance(copied["id"], str)
    assert copied["etag"] == original["etag"]
    assert (copied["width"], copied["height"], copied["size"]) == (37, 19, len(png()))
    assert owner.get(f"{ROOT}/{copied['id']}/file").content == png()
    assert member.get(f"{ROOT}/{copied['id']}/file").status_code == 404
    assert member.get(f"{ROOT}/{original['id']}/file").status_code == 404
    document["nodes"] = [image_node(copied)]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "attach-copy"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    assert member.get(f"{ROOT}/{copied['id']}/file").content == png()
    assert member.get(f"{ROOT}/{original['id']}/file").status_code == 404
    assert owner.delete(ASSETS + "/original").status_code == 200
    assert cleanup(resource_app, apply=True)["completed"] == 1
    assert owner.get(f"{ROOT}/{original['id']}/file").status_code == 404
    assert copy_resource(owner, original, document["id"]).json() == response.json()
    assert owner.get(f"{ROOT}/{copied['id']}/file").content == png()
    with resource_app[1]() as session:
        row = session.scalar(select(CanvasResourceCopySource))
        provenance_id = row.id
        assert row.original_resource_id == int(original["id"])
        assert row.source_media_id is None and row.released_at is not None
    with resource_app[1]() as session:
        session.info["actor"] = SimpleNamespace(user_id=int(member_user["id"]))
        assert session.get(CanvasResourceCopySource, provenance_id) is None
    with resource_app[1]() as session:
        session.info["actor"] = SimpleNamespace(user_id=int(user["id"]))
        source = session.get(CanvasResourceCopySource, provenance_id)
        source.snapshot_json = {}
        with pytest.raises(WorkflowError, match="fixed"):
            session.flush()
        session.rollback()


@pytest.mark.parametrize("file_kind", ["image", "file"])
def test_copy_unknown_response_retry_and_deletion_tombstone(resource_app, monkeypatch, file_kind):
    owner, _ = account(resource_app, "copy_retry")
    _, _, document = canvas(owner, "copy-retry-target")
    if file_kind == "image":
        body, options = png(), {}
    else:
        body, options = (
            b"glTF-copy-payload",
            {"kind": "file", "name": "model.glb", "mime": "model/gltf-binary"},
        )
    original = upload(owner, body, **options).json()["resource"]
    register(owner, "retry-original", original)
    storage = resource_app[0].state.storage
    actual_copy = storage.copy
    calls = []

    def lost_ack(*args):
        result = actual_copy(*args)
        calls.append(args)
        if len(calls) == 1:
            raise StorageUnavailable("injected lost copy acknowledgement")
        return result

    monkeypatch.setattr(storage, "copy", lost_ack)
    failed = copy_resource(owner, original, document["id"])
    assert failed.status_code == 503, failed.text
    pending, source = pending_copy(resource_app)
    assert pending.status == "pending" and source.released_at is None
    assert source.source_media_id or source.source_binary_id
    # Bytes exist, but an uncommitted copy is not a readable resource.
    assert owner.get(f"{ROOT}/{pending.reserved_resource_id}/file").status_code == 404
    rejected = owner.delete(ASSETS + "/retry-original")
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "canvas_asset_in_use"
    recovered = copy_resource(owner, original, document["id"])
    assert recovered.status_code == 200, recovered.text
    copied = recovered.json()["resource"]
    assert copied["id"] == str(pending.reserved_resource_id)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert copy_resource(owner, original, document["id"]).json() == recovered.json()
    assert len(calls) == 2
    assert owner.get(f"{ROOT}/{copied['id']}/file").content == body
    register(owner, "retry-copy", copied)
    assert owner.delete(ASSETS + "/retry-copy").status_code == 200
    assert cleanup(resource_app, apply=True)["completed"] == 1
    deleted = copy_resource(owner, original, document["id"])
    assert deleted.status_code == 410, deleted.text
    assert deleted.json()["error"]["code"] == "canvas_resource_deleted"
    assert owner.get(f"{ROOT}/{original['id']}/file").content == body
    replacement = copy_resource(owner, original, document["id"], key="explicit-new-copy")
    assert replacement.status_code == 200 and replacement.json()["resource"]["id"] != copied["id"]


def test_two_requests_with_same_key_copy_once_and_changed_content_conflicts(
    resource_app, monkeypatch
):
    owner, _ = account(resource_app, "copy_concurrent")
    _, _, document = canvas(owner, "copy-concurrent-target")
    original = upload(owner, png()).json()["resource"]
    other = upload(owner, png("blue"), key="other-upload").json()["resource"]
    storage = resource_app[0].state.storage
    actual_copy, calls = storage.copy, []

    def tracked(*args):
        calls.append(args)
        return actual_copy(*args)

    monkeypatch.setattr(storage, "copy", tracked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: copy_resource(owner, original, document["id"]), range(2)))
    assert [result.status_code for result in results] == [200, 200], [r.text for r in results]
    assert results[0].json() == results[1].json() and len(calls) == 1
    conflict = copy_resource(owner, other, document["id"])
    assert (
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "canvas_upload_conflict"
    )


def test_expired_copy_cleanup_releases_source_and_same_request_can_resume(
    resource_app, monkeypatch
):
    owner, _ = account(resource_app, "copy_expired")
    _, _, document = canvas(owner, "copy-expired-target")
    original = upload(owner, png()).json()["resource"]
    storage = resource_app[0].state.storage
    actual_copy = storage.copy

    def unknown(*args):
        actual_copy(*args)
        raise StorageUnavailable("injected lost acknowledgement")

    monkeypatch.setattr(storage, "copy", unknown)
    assert copy_resource(owner, original, document["id"]).status_code == 503
    row, _ = pending_copy(resource_app)
    with resource_app[1].begin() as session:
        session.get(CanvasResourceUpload, row.id).expires_at = utcnow() - timedelta(seconds=1)
    cleaned = cleanup_canvas_uploads(resource_app[1], storage, resource_app[2], apply=True)
    assert cleaned["failed"] == 0 and cleaned["remove_requests"] == 1
    _, source = pending_copy(resource_app)
    assert source.source_media_id is None and source.released_at is not None
    with pytest.raises(NotFound):
        storage.stat(
            resource_app[2].minio_image_bucket, f"canvas/resources/{row.reserved_resource_id}"
        )
    monkeypatch.setattr(storage, "copy", actual_copy)
    resumed = copy_resource(owner, original, document["id"])
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["resource"]["id"] == str(row.reserved_resource_id)


@pytest.mark.parametrize("revoke_scope", ["source", "target"])
def test_source_privacy_and_revocation_during_copy_prevent_publication(
    resource_app, monkeypatch, revoke_scope
):
    owner, _ = account(resource_app, "copy_source_owner")
    member, member_user = account(resource_app, "copy_source_member")
    source_project, source_path, source_doc = canvas(owner, "source")
    join(resource_app, owner, member, source_project["id"], member_user["id"])
    target_project, _, target_doc = canvas(owner, "target")
    join(resource_app, owner, member, target_project["id"], member_user["id"])
    original = upload(owner, png(), source=source_doc["id"]).json()["resource"]
    assert copy_resource(member, original, target_doc["id"]).status_code == 404
    source_doc["nodes"] = [image_node(original)]
    saved = owner.post(
        source_path + "/commits",
        headers={"Idempotency-Key": "publish-source"},
        json={"expected_row_version": source_doc["revision"], "source_document": source_doc},
    )
    assert saved.status_code == 200, saved.text
    storage = resource_app[0].state.storage
    copied, finish = Event(), Event()
    actual_copy = storage.copy

    def revoke_before_commit(*args):
        result = actual_copy(*args)
        copied.set()
        assert finish.wait(10), "revocation did not finish"
        return result

    monkeypatch.setattr(storage, "copy", revoke_before_commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(copy_resource, member, original, target_doc["id"])
        assert copied.wait(10)
        project_id = source_project["id"] if revoke_scope == "source" else target_project["id"]
        revoke = owner.delete(f"/api/v1/projects/{project_id}/members/{member_user['id']}")
        finish.set()
        assert revoke.status_code in {200, 204}, revoke.text
        result = future.result(timeout=15)
    assert result.status_code == 404, result.text
    row, source = pending_copy(resource_app)
    assert row.status == "pending" and source.released_at is None
    assert member.get(f"{ROOT}/{row.reserved_resource_id}/file").status_code == 404
    with resource_app[1]() as session:
        assert session.get(MediaFile, row.reserved_resource_id) is None


def test_expiry_cleaner_cannot_delete_bytes_written_by_an_active_copy(resource_app, monkeypatch):
    owner, _ = account(resource_app, "copy_cleanup_race")
    _, _, document = canvas(owner, "copy-cleanup-target")
    original = upload(owner, png()).json()["resource"]
    storage = resource_app[0].state.storage
    written, finish = Event(), Event()
    actual_copy = storage.copy

    def pause_after_write(*args):
        result = actual_copy(*args)
        written.set()
        assert finish.wait(15), "cleanup did not finish its bounded lock attempt"
        return result

    monkeypatch.setattr(storage, "copy", pause_after_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(copy_resource, owner, original, document["id"])
        assert written.wait(10)
        row, _ = pending_copy(resource_app)
        with resource_app[1].begin() as session:
            session.get(CanvasResourceUpload, row.id).expires_at = utcnow() - timedelta(seconds=1)
        try:
            cleanup_result = cleanup_canvas_uploads(
                resource_app[1], storage, resource_app[2], apply=True
            )
            assert cleanup_result["failed"] == 1 and cleanup_result["remove_requests"] == 0
            _, source = pending_copy(resource_app)
            assert source.source_media_id == int(original["id"]) and source.released_at is None
        finally:
            finish.set()
        response = future.result(timeout=10)
    assert response.status_code == 200, response.text
    assert owner.get(f"{ROOT}/{row.reserved_resource_id}/file").content == png()
    assert (
        cleanup_canvas_uploads(resource_app[1], storage, resource_app[2], apply=True)["failed"] == 0
    )
    assert owner.get(f"{ROOT}/{row.reserved_resource_id}/file").content == png()


def test_actual_audio_and_video_copies_keep_intrinsic_metadata(resource_app, tmp_path):
    owner, _ = account(resource_app, "copy_actual_av")
    _, _, document = canvas(owner, "copy-av-target")
    audio = BytesIO()
    with wave.open(audio, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(bytes(16000))
    video = tmp_path / "source.mp4"
    result = subprocess.run(
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
        check=False,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    for kind, body, name, mime in (
        ("audio", audio.getvalue(), "sound.wav", "audio/wav"),
        ("video", video.read_bytes(), "source.mp4", "video/mp4"),
    ):
        response = upload(owner, body, key=kind, kind=kind, name=name, mime=mime)
        assert response.status_code == 200, response.text
        original = response.json()["resource"]
        response = copy_resource(owner, original, document["id"], key="copy-" + kind)
        assert response.status_code == 200, response.text
        copied = response.json()["resource"]
        for field in ("width", "height", "durationMs", "etag", "size", "mimeType"):
            assert copied[field] == original[field]
        assert copied["durationMs"] == 1000
        assert owner.get(f"{ROOT}/{copied['id']}/file").content == body


def test_changed_source_during_copy_cannot_commit_or_reuse_old_request(resource_app, monkeypatch):
    owner, _ = account(resource_app, "copy_source_changed")
    _, _, document = canvas(owner, "copy-changed-target")
    original = upload(owner, png()).json()["resource"]
    storage = resource_app[0].state.storage
    actual_copy = storage.copy

    def change_source_after_copy(*args):
        result = actual_copy(*args)
        with resource_app[1].begin() as session:
            source = session.get(MediaFile, int(original["id"]))
            source.original_name = "renamed.png"
        return result

    monkeypatch.setattr(storage, "copy", change_source_after_copy)
    rejected = copy_resource(owner, original, document["id"])
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_resource_copy_changed"
    row, source = pending_copy(resource_app)
    assert row.status == "pending" and source.source_media_id == int(original["id"])
    assert source.snapshot_json["original_name"] == "image.png"
    assert owner.get(f"{ROOT}/{row.reserved_resource_id}/file").status_code == 404
    monkeypatch.setattr(storage, "copy", actual_copy)
    repeated = copy_resource(owner, original, document["id"])
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "canvas_resource_copy_changed"
    replacement = copy_resource(owner, original, document["id"], key="explicit-changed-source")
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["resource"]["id"] != str(row.reserved_resource_id)
    assert owner.get(f"{ROOT}/{replacement.json()['resource']['id']}/file").content == png()


def test_final_sql_failure_keeps_reserved_copy_and_source_pin_for_retry(resource_app):
    owner, _ = account(resource_app, "copy_commit_rollback")
    _, _, document = canvas(owner, "copy-rollback-target")
    original = upload(owner, png()).json()["resource"]
    register(owner, "rollback-original", original)
    with resource_app[1]() as session:
        engine = session.get_bind().engine

    def reject_source_release(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("UPDATE canvas_resource_copy_sources"):
            raise RuntimeError("injected copy completion SQL failure")

    event.listen(engine, "before_cursor_execute", reject_source_release)
    try:
        with pytest.raises(RuntimeError, match="injected copy completion SQL failure"):
            copy_resource(owner, original, document["id"])
    finally:
        event.remove(engine, "before_cursor_execute", reject_source_release)
    row, source = pending_copy(resource_app)
    assert row.status == "pending" and row.media_id is None
    assert source.source_media_id == int(original["id"]) and source.released_at is None
    with resource_app[1]() as session:
        assert session.get(MediaFile, row.reserved_resource_id) is None
    assert owner.get(f"{ROOT}/{row.reserved_resource_id}/file").status_code == 404
    assert owner.delete(ASSETS + "/rollback-original").status_code == 409
    recovered = copy_resource(owner, original, document["id"])
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["resource"]["id"] == str(row.reserved_resource_id)
    assert owner.get(f"{ROOT}/{row.reserved_resource_id}/file").content == png()
    assert owner.delete(ASSETS + "/rollback-original").status_code == 200
    assert cleanup(resource_app, apply=True)["completed"] == 1
    assert owner.get(f"{ROOT}/{row.reserved_resource_id}/file").content == png()
