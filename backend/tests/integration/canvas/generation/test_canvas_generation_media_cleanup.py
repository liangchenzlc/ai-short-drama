"""真实HTTP/MySQL/MinIO生成媒体回收；Gateway受控，不代表收费供应商。"""

import base64
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import event, select, text, update

from scripts.canvas_migration import apply, inspect_schema
from short_drama.ai import GenerationResult
from short_drama.core.exceptions import NotFound, StorageUnavailable, WorkflowError
from short_drama.dao.canvas.generation.generation_media_cleanup import (
    CanvasGenerationMediaCleanupDAO,
)
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasBinaryResource,
    CanvasGenerationMediaDeletion,
    CanvasResourceDeletion,
    CanvasResourceUpload,
    CanvasResult,
    MediaAsset,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.canvas.document.canvas import CanvasWriteReceipt
from short_drama.service.base import utcnow
from short_drama.service.canvas.generation.generation_media_cleanup import (
    cleanup_canvas_generation_media,
    retire_canvas_generation_media,
)
from short_drama.service.generations.execution.execution import GenerationExecutionService
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id
from tests.integration.canvas.document.test_canvas_workspace import actor
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    admit,
    bind,
    seed,
)
from tests.integration.canvas.resources.test_canvas_library_deletion import ASSETS, register
from tests.integration.canvas.resources.test_canvas_media_locators import save
from tests.integration.canvas.resources.test_canvas_recycle import archive
from tests.integration.canvas.resources.test_canvas_recycle_catalog import purge
from tests.integration.canvas.resources.test_canvas_recycle_cleanup import cleanup, dispose
from tests.integration.canvas.resources.test_canvas_recycle_execution_cleanup import (
    keep_project_active,
)
from tests.integration.canvas.resources.test_canvas_resources import ROOT, png
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = pytest.mark.integration
minio_required = pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1", reason="生成媒体回收需显式启用隔离MinIO"
)


def generate(app, name, *, count=1, client=None, user=None, project=None):
    owner, user, project, path, request = seed(
        app, name, kind="image", client=client, user=user, project=project
    )
    keep_project_active(owner, project)
    request["input"]["config"]["count"] = str(count)
    task = admit(owner, request)
    bodies = [png(color) for color in ("purple", "blue")[:count]]

    class Gateway:
        calls = 0

        def validate(self, *_args):
            return {}

        def submit(self, *_args, **_kwargs):
            self.calls += 1
            return GenerationResult(
                "succeeded",
                "openai_images.v1",
                outputs=[{"base64": base64.b64encode(body).decode()} for body in bodies],
            )

    gateway = Gateway()
    executor = GenerationExecutionService(app[1], app[2], gateway, app[0].state.storage)
    executor.execute(task["id"], 1)
    with app[1]() as session:
        version = session.get(AsyncTask, int(task["id"])).message_version
    executor.execute(task["id"], version)
    with app[1]() as session:
        rows = list(
            session.execute(
                select(MediaAsset, MediaFile)
                .join(MediaFile, MediaFile.id == MediaAsset.media_id)
                .order_by(MediaAsset.output_index)
            )
        )
        assert len(rows) == count
        outputs = [
            {"asset": asset.id, "media": media.id, "locator": media.storage_locator, "body": body}
            for (asset, media), body in zip(rows, bodies, strict=True)
        ]
        assert session.get(AsyncTask, int(task["id"])).status == "succeeded"
    return owner, user, project, path, request, task, outputs, gateway


def dispose_generated(app, scene):
    owner, _, _, path, _, _, _, _ = scene
    document = owner.get(path + "/my-document").json()["source_document"]
    dispose(app, owner, path, document)
    assert cleanup(app)["completed"] == 1


def retire(app, *, apply=True):
    return retire_canvas_generation_media(app[1], app[2], apply=apply)


def drain(app, *, apply=True):
    return cleanup_canvas_generation_media(app[1], app[0].state.storage, app[2], apply=apply)


def location(app, output):
    return ObjectLocation.parse(output["locator"], {app[2].minio_image_bucket})


def bytes_present(app, output):
    key = location(app, output)
    with app[0].state.storage.open(key.bucket, key.object_name) as stream:
        return stream.read()


@minio_required
def test_two_unbound_outputs_retire_atomically_and_preserve_audit_and_old_request(resource_app):
    scene = generate(resource_app, "generation_recycle_unbound", count=2)
    owner, _, _, _, request, task, outputs, gateway = scene
    dispose_generated(resource_app, scene)
    with resource_app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        proof = deepcopy(record.response_data)
        receipts = [
            (row.id, deepcopy(row.result_json))
            for row in session.scalars(select(CanvasWriteReceipt))
        ]
        assert session.scalar(select(CanvasResourceUpload)) is None
    checked = retire(resource_app, apply=False)
    assert checked["eligible"] == 2 and checked["retired"] == 0 and checked["failed"] == 0
    assert all(bytes_present(resource_app, item) == item["body"] for item in outputs)
    result = retire(resource_app)
    assert result["retired"] == 2 and result["failed"] == result["retained"] == 0
    with resource_app[1]() as session:
        assert session.scalar(select(MediaAsset)) is None
        assert session.scalar(select(MediaFile)) is None
        assert session.scalar(select(CanvasResourceDeletion)) is None
        assert session.scalar(select(AIGenerationRecord)).response_data == proof
        assert [
            (row.id, row.result_json) for row in session.scalars(select(CanvasWriteReceipt))
        ] == receipts
        jobs = list(
            session.scalars(
                select(CanvasGenerationMediaDeletion).order_by(
                    CanvasGenerationMediaDeletion.output_index
                )
            )
        )
        assert [(job.asset_id, job.resource_id, job.status) for job in jobs] == [
            (item["asset"], item["media"], "pending") for item in outputs
        ]
        assert all(job.asset_id != job.resource_id for job in jobs)
    assert all(bytes_present(resource_app, item) == item["body"] for item in outputs)
    assert drain(resource_app, apply=False)["examined"] == 2
    assert drain(resource_app)["completed"] == 2
    for output in outputs:
        with pytest.raises(NotFound):
            bytes_present(resource_app, output)
    assert retire(resource_app)["examined"] == drain(resource_app)["examined"] == 0
    assert owner.post(TASKS, json=request).status_code == 404
    assert owner.post(f"/api/v1/ai/generations/{task['id']}/resume").status_code == 409
    assert gateway.calls == 1


@minio_required
def test_bound_receipt_and_independent_library_protect_until_last_reference_is_released(
    resource_app,
):
    scene = generate(resource_app, "generation_recycle_member")
    owner, _, project, _, request, task, outputs, _ = scene
    member, member_user = account(resource_app, "generation_recycle_member_ref")
    outsider, _ = account(resource_app, "generation_recycle_outsider")
    bound = bind(owner, request, task)
    detail = owner.get(f"{TASKS}/{task['id']}").json()
    own_library = detail["outputs"][0]["materializedAssetId"]
    output = outputs[0]
    register(
        owner,
        "independent-generated-copy",
        {
            "id": str(output["media"]),
            "kind": "image",
            "size": len(output["body"]),
            "mimeType": "image/png",
            "width": 37,
            "height": 19,
        },
    )
    assert outsider.get(f"{ROOT}/{output['media']}/file").status_code == 404
    dispose_generated(resource_app, scene)
    assert retire(resource_app)["retained"] == 1
    assert owner.delete(f"{ASSETS}/{own_library}").status_code == 200
    assert retire(resource_app)["retained"] == 1
    assert member.get(f"{ROOT}/{output['media']}/file").status_code == 404
    assert owner.get(f"{ROOT}/{output['media']}/file").content == output["body"]
    assert owner.delete(f"{ASSETS}/independent-generated-copy").status_code == 200
    assert retire(resource_app)["retired"] == 1
    with resource_app[1]() as session:
        assert session.scalar(select(CanvasResult)) is None
        public = next(
            row
            for row in session.scalars(select(CanvasWriteReceipt))
            if row.operation_kind == "canvas.task.bind"
        )
        assert public.result_json == bound["result"]["historical"]
    assert drain(resource_app)["completed"] == 1


@minio_required
@pytest.mark.parametrize("lost_response", [False, True])
def test_failed_or_unknown_remove_retries_exact_object_and_concurrent_drain_finishes_once(
    resource_app, monkeypatch, lost_response
):
    scene = generate(resource_app, "generation_recycle_retry")
    output = scene[6][0]
    dispose_generated(resource_app, scene)
    assert retire(resource_app)["retired"] == 1
    storage = resource_app[0].state.storage
    original = storage.remove

    def unavailable(bucket, object_name):
        if lost_response:
            original(bucket, object_name)
        raise StorageUnavailable("injected generation remove response loss")

    with monkeypatch.context() as fault:
        fault.setattr(storage, "remove", unavailable)
        assert drain(resource_app)["failed"] == 1
    with resource_app[1].begin() as session:
        job = session.scalar(select(CanvasGenerationMediaDeletion))
        assert job.status == "pending" and job.attempts == 1 and job.completed_at is None
        assert job.storage_locator == output["locator"]
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: drain(resource_app), range(2)))
    assert sum(result["completed"] for result in results) == 1
    assert sum(result["failed"] for result in results) == 0
    with pytest.raises(NotFound):
        bytes_present(resource_app, output)
    with resource_app[1]() as session:
        assert session.scalar(select(CanvasGenerationMediaDeletion)).status == "completed"


@minio_required
@pytest.mark.parametrize("field", ["asset_id", "locator", "checksum", "mime", "saved"])
def test_mismatched_archive_evidence_preserves_canonical_media_and_does_not_enqueue(
    resource_app, field
):
    scene = generate(resource_app, "generation_recycle_evidence")
    output = scene[6][0]
    dispose_generated(resource_app, scene)
    with resource_app[1].begin() as session:
        record = session.scalar(select(AIGenerationRecord))
        original = deepcopy(record.response_data)
        changed = deepcopy(original)
        changed["media_manifest"][0][field] = {
            "asset_id": str(output["media"]),
            "locator": output["locator"] + "-other",
            "checksum": "0" * 64,
            "mime": "image/jpeg",
            "saved": False,
        }[field]
        record.response_data = changed
    assert retire(resource_app)["failed"] == 1
    with resource_app[1].begin() as session:
        assert session.get(MediaFile, output["media"]) is not None
        assert session.get(MediaAsset, output["asset"]) is not None
        assert session.scalar(select(CanvasGenerationMediaDeletion)) is None
        session.scalar(select(AIGenerationRecord)).response_data = original
    assert bytes_present(resource_app, output) == output["body"]
    assert retire(resource_app)["retired"] == 1
    assert drain(resource_app)["completed"] == 1


@minio_required
def test_retirement_commit_failure_rolls_back_canonical_output_and_outbox(
    resource_app, monkeypatch
):
    scene = generate(resource_app, "generation_recycle_rollback")
    output = scene[6][0]
    dispose_generated(resource_app, scene)
    session_type = resource_app[1].class_

    def fail(session):
        assert session.get(MediaFile, output["media"]) is None
        assert session.get(MediaAsset, output["asset"]) is None
        assert session.scalar(select(CanvasGenerationMediaDeletion)) is not None
        raise RuntimeError("injected generation retirement commit failure")

    event.listen(session_type, "before_commit", fail)
    try:
        result = retire(resource_app)
        assert result["failed"] == 1 and result["retired"] == 0
    finally:
        event.remove(session_type, "before_commit", fail)
    with resource_app[1]() as session:
        assert session.get(MediaFile, output["media"]) is not None
        assert session.get(MediaAsset, output["asset"]) is not None
        assert session.scalar(select(CanvasGenerationMediaDeletion)) is None
    assert bytes_present(resource_app, output) == output["body"]
    assert retire(resource_app)["retired"] == drain(resource_app)["completed"] == 1


@minio_required
def test_concurrent_retirement_enqueues_only_one_output(resource_app, monkeypatch):
    scene = generate(resource_app, "generation_recycle_race")
    dispose_generated(resource_app, scene)
    barrier, original = Barrier(2), CanvasGenerationMediaCleanupDAO.candidates

    def synchronize(dao, after_id):
        result = original(dao, after_id)
        if after_id == 0:
            barrier.wait(timeout=10)
        return result

    with monkeypatch.context() as fault:
        fault.setattr(CanvasGenerationMediaCleanupDAO, "candidates", synchronize)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: retire(resource_app), range(2)))
    assert sum(result["retired"] for result in results) == 1
    assert sum(result["failed"] for result in results) == 0
    assert drain(resource_app)["completed"] == 1


@minio_required
def test_other_account_canonical_locator_defers_bytes_and_remains_retryable(resource_app):
    scene = generate(resource_app, "generation_recycle_locator")
    output = scene[6][0]
    _, other = account(resource_app, "generation_recycle_locator_other")
    dispose_generated(resource_app, scene)
    assert retire(resource_app)["retired"] == 1
    now, alias_id = utcnow(), next_id()
    with resource_app[1].begin() as session:
        session.add(
            CanvasBinaryResource(
                id=alias_id,
                scope_user_id=int(other["id"]),
                project_id=None,
                created_by=int(other["id"]),
                updated_by=int(other["id"]),
                created_at=now,
                updated_at=now,
                resource_kind="file",
                mime_type="application/octet-stream",
                storage_locator=output["locator"],
                original_name="protected.png",
                byte_size=len(output["body"]),
                checksum_sha256="0" * 64,
            )
        )
    assert drain(resource_app)["retained"] == 1
    assert bytes_present(resource_app, output) == output["body"]
    with resource_app[1].begin() as session:
        job = session.scalar(select(CanvasGenerationMediaDeletion))
        assert job.status == "pending" and job.completed_at is None
        session.delete(session.get(CanvasBinaryResource, alias_id))
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
    assert drain(resource_app)["completed"] == 1


@minio_required
def test_project_owner_does_not_retire_another_authors_generation(resource_app):
    owner, _, project, path, _ = seed(resource_app, "generation_recycle_author_owner", kind="image")
    member, user = account(resource_app, "generation_recycle_author_member")
    scene = generate(resource_app, "unused", client=member, user=user)
    output = scene[6][0]
    document = owner.get(path + "/my-document").json()["source_document"]
    dispose(resource_app, owner, path, document)
    assert cleanup(resource_app)["completed"] == 1
    assert retire(resource_app)["examined"] == 0
    assert bytes_present(resource_app, output) == output["body"]
    with resource_app[1]() as session:
        assert session.get(MediaAsset, output["asset"]) is not None
        assert session.get(AsyncTask, int(scene[5]["id"])).error is None


def test_actor_session_cannot_run_generation_media_maintenance(identity_app):
    _, user = account(identity_app, "generation_recycle_actor")
    factory = identity_app[1]
    with factory(info={"actor": actor(int(user["id"]))}) as session:
        with pytest.raises(ValueError, match="internal maintenance"):
            CanvasGenerationMediaCleanupDAO(session)
        with pytest.raises(WorkflowError, match="checked entity"):
            session.execute(update(CanvasGenerationMediaDeletion).values(status="completed"))


@minio_required
def test_one_retired_output_does_not_pin_sibling_after_independent_library_release(resource_app):
    scene = generate(resource_app, "generation_recycle_sibling", count=2)
    owner, outputs = scene[0], scene[6]
    register(
        owner,
        "retained-sibling",
        {
            "id": str(outputs[0]["media"]),
            "kind": "image",
            "size": len(outputs[0]["body"]),
            "mimeType": "image/png",
            "width": 37,
            "height": 19,
        },
    )
    dispose_generated(resource_app, scene)
    first = retire(resource_app)
    assert first["retired"] == first["retained"] == 1 and first["failed"] == 0
    assert drain(resource_app)["completed"] == 1
    assert bytes_present(resource_app, outputs[0]) == outputs[0]["body"]
    assert owner.delete(f"{ASSETS}/retained-sibling").status_code == 200
    assert retire(resource_app)["retired"] == drain(resource_app)["completed"] == 1


@minio_required
def test_other_canvas_and_its_history_keep_generated_media_until_their_own_disposal(resource_app):
    scene = generate(resource_app, "generation_recycle_other_canvas")
    owner, _, project, path, request, task, outputs, _ = scene
    bind(owner, request, task)
    library = owner.get(f"{TASKS}/{task['id']}").json()["outputs"][0]["materializedAssetId"]
    output = outputs[0]
    with resource_app[1]() as session:
        sibling = session.scalar(
            select(ProjectCanvas).where(
                ProjectCanvas.project_id == int(project["id"]),
                ProjectCanvas.id != int(project["primary_canvas_id"]),
            )
        )
        sibling_path = f"/api/v1/projects/{project['id']}/canvases/{sibling.id}"
    document = owner.get(sibling_path + "/my-document").json()["source_document"]
    document["nodes"] = [
        {
            "id": "shared-generated",
            "type": "image",
            "title": "另一作品使用的生成图",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 200,
            "metadata": {"storageKey": "resource:" + str(output["media"])},
        }
    ]
    saved = save(owner, sibling_path, document, "generated-reference-other-canvas")
    assert owner.delete(f"{ASSETS}/{library}").status_code == 200
    dispose_generated(resource_app, scene)
    assert retire(resource_app)["retained"] == 1
    assert bytes_present(resource_app, output) == output["body"]
    archive_key = "other-canvas-own-disposal"
    archive(owner, sibling_path, saved, archive_key)
    purged = purge(owner, saved["id"], archive_key, key="other-canvas-purge")
    assert purged.status_code == 200, purged.text
    assert cleanup(resource_app)["completed"] == 1
    assert retire(resource_app)["retired"] == drain(resource_app)["completed"] == 1
    assert owner.get(path + "/my-document").status_code == 404


@minio_required
@pytest.mark.parametrize("change", ["locator", "author", "unknown"])
def test_drain_rechecks_trusted_receipt_and_archive_before_removing_bytes(resource_app, change):
    scene = generate(resource_app, "generation_recycle_worker_proof")
    output = scene[6][0]
    _, other = account(resource_app, "generation_recycle_worker_other")
    dispose_generated(resource_app, scene)
    assert retire(resource_app)["retired"] == 1
    with resource_app[1].begin() as session:
        job = session.scalar(select(CanvasGenerationMediaDeletion))
        record = session.scalar(select(AIGenerationRecord))
        original_user, original_status = job.user_id, record.status
        if change == "locator":
            job.storage_locator = output["locator"] + "-other"
        elif change == "author":
            job.user_id = int(other["id"])
        else:
            record.status = "unknown"
    assert drain(resource_app)["failed"] == 1
    assert bytes_present(resource_app, output) == output["body"]
    with resource_app[1].begin() as session:
        job = session.scalar(select(CanvasGenerationMediaDeletion))
        assert job.status == "pending" and job.attempts == 1
        job.storage_locator, job.user_id = output["locator"], original_user
        session.scalar(select(AIGenerationRecord)).status = original_status
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
    assert drain(resource_app)["completed"] == 1


@minio_required
def test_actor_cannot_forge_internal_outbox_even_using_an_active_sibling_canvas(resource_app):
    scene = generate(resource_app, "generation_recycle_internal")
    dispose_generated(resource_app, scene)
    assert retire(resource_app)["retired"] == 1
    with resource_app[1]() as session:
        row = session.scalar(select(CanvasGenerationMediaDeletion))
        values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        sibling = session.scalar(select(ProjectCanvas).where(ProjectCanvas.archived_at.is_(None)))
        values.update(id=next_id(), canvas_id=sibling.id)
    with resource_app[1](info={"actor": actor(int(scene[1]["id"]))}) as session:
        session.add(CanvasGenerationMediaDeletion(**values))
        # 普通Session也可能先由既有资源范围检查返回404；两种路径都必须拒绝写入。
        with pytest.raises((WorkflowError, NotFound)):
            session.flush()
        session.rollback()
    with resource_app[1]() as session:
        assert session.get(CanvasGenerationMediaDeletion, values["id"]) is None
    assert drain(resource_app)["completed"] == 1


def test_generation_media_migration_is_additive_reentrant_and_rejects_drift(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE canvas_generation_media_deletions"))
        before = inspect_schema(connection)
    assert (
        before["missing"] == ["table:canvas_generation_media_deletions"] and before["changed"] == []
    )
    result = apply(engine)
    assert result["status"] == "ready" and result["executed"] == [
        "table:canvas_generation_media_deletions",
        "index:canvas_generation_media_deletions.idx_canvas_generation_delete_due",
    ]
    assert apply(engine)["executed"] == []
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE canvas_generation_media_deletions "
                "DROP CHECK ck_canvas_generation_delete_state"
            )
        )
    with pytest.raises(RuntimeError, match="结构与 ORM 不符"):
        apply(engine)
