"""真实HTTP/MySQL/MinIO回收；任务状态和失败由测试控制，不调用模型。"""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import event, func, select

from short_drama.core.exceptions import NotFound, StorageUnavailable
from short_drama.dao.canvas_recycle_cleanup_dao import CanvasRecycleCleanupDAO
from short_drama.dao.task_runtime_dao import finish
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    Base,
    CanvasBinaryResource,
    CanvasLocalJob,
    CanvasRenderJob,
    CanvasResourceDeletion,
    CanvasResourceUpload,
    CanvasToolRun,
    MediaFile,
)
from short_drama.domain.canvas import CanvasWriteReceipt
from short_drama.service.base import utcnow
from short_drama.service.canvas_recycle_cleanup import cleanup_canvas_recycle
from short_drama.service.canvas_resource_cleanup import cleanup_canvas_resources
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id
from tests.integration.canvas.document.test_canvas_workspace import actor, node
from tests.integration.canvas.drawing.test_canvas_drawing_history import history_scene
from tests.integration.canvas.generation.test_canvas_generation_runtime import admit, seed
from tests.integration.canvas.projects.test_canvas_project_folders import create_canvas
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_library_deletion import ASSETS, register
from tests.integration.canvas.resources.test_canvas_media_locators import save
from tests.integration.canvas.resources.test_canvas_recycle import archive, archive_status, restore
from tests.integration.canvas.resources.test_canvas_recycle_catalog import CATALOG, purge
from tests.integration.canvas.resources.test_canvas_resource_copy import image_node
from tests.integration.canvas.resources.test_canvas_resources import ROOT, png, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1", reason="回收清理需显式启用隔离MinIO"
    ),
]


def payload_counts(app, canvas_id):
    # 独立核对用户实际写入的文档和历史表，不以清理器的删除名单作为预期。
    tables = (
        "canvas_nodes",
        "canvas_edges",
        "canvas_timelines",
        "canvas_director_scenes",
        "canvas_drawings",
        "canvas_drawing_versions",
        "canvas_drawing_media_references",
        "canvas_revisions",
        "canvas_revision_drawing_references",
        "canvas_revision_media_references",
        "canvas_revision_user_states",
        "canvas_node_user_states",
        "canvas_user_states",
    )
    with app[1]() as session:
        return {
            name: session.scalar(
                select(func.count()).select_from(table).where(table.c.canvas_id == canvas_id)
            )
            for name in tables
            for table in (Base.metadata.tables[name],)
        }


def receipts(app, canvas_id):
    with app[1]() as session:
        return {
            row.id: (row.request_hash, row.expected_row_version, deepcopy(row.result_json))
            for row in session.scalars(
                select(CanvasWriteReceipt).where(CanvasWriteReceipt.canvas_id == canvas_id)
            )
        }


def dispose(app, client, path, document, key="cleanup-archive"):
    result = archive(client, path, document, key)
    response = purge(client, document["id"], key)
    assert response.status_code == 200, response.text
    return result, response.json()


def cleanup(app, *, apply=True):
    return cleanup_canvas_recycle(app[1], app[2], apply=apply)


def remove_bytes(app):
    return cleanup_canvas_resources(app[1], app[0].state.storage, app[2], apply=True)


def test_graph_drawing_history_uploads_and_bytes_are_removed_with_receipt_replay(
    resource_app, monkeypatch, database_errors
):
    client, project, path, document, _, _, resource = history_scene(resource_app, "purge_payload")
    binary = upload(
        client,
        b"graph-owned-file",
        key="purge-binary",
        source=document["id"],
        kind="file",
        name="note.txt",
        mime="text/plain",
    )
    assert binary.status_code == 200, binary.text
    binary_id = int(binary.json()["resource"]["id"])
    document["nodes"][0]["parentId"] = "parent"
    document["nodes"].extend([node("parent"), node("child", parent="parent")])
    document["connections"] = [{"id": "edge", "fromNodeId": "parent", "toNodeId": "child"}]
    document["timeline"] = {
        "clips": [
            {
                "id": "clip",
                "directMedia": {"kind": "image", "storageKey": "resource:" + resource["id"]},
            }
        ]
    }
    document["directorScenes"] = [{"id": "scene", "name": "待回收场景", "objects": [], "shots": []}]
    document = save(client, path, document, "purge-composite-graph")
    archive_result, purge_result = dispose(resource_app, client, path, document)
    canvas_id = int(project["primary_canvas_id"])
    before = payload_counts(resource_app, canvas_id)
    frozen = receipts(resource_app, canvas_id)
    assert before["canvas_nodes"] and before["canvas_drawing_versions"] >= 2
    assert before["canvas_revisions"] and before["canvas_revision_drawing_references"]
    assert before["canvas_nodes"] == 3
    assert (
        before["canvas_edges"]
        == before["canvas_timelines"]
        == before["canvas_director_scenes"]
        == 1
    )
    assert cleanup(resource_app, apply=False)["examined"] == 1
    assert payload_counts(resource_app, canvas_id) == before
    assert receipts(resource_app, canvas_id) == frozen
    assert cleanup(resource_app) == {
        "examined": 1,
        "completed": 1,
        "deferred": 0,
        "retired_uploads": 2,
        "retained_uploads": 0,
        "retired_creation_resources": 0,
        "retained_creation_resources": 0,
        "failed": 0,
        "applied": True,
    }
    assert not any(payload_counts(resource_app, canvas_id).values())
    for identifier, (digest, version, result) in receipts(resource_app, canvas_id).items():
        assert (digest, version) == frozen[identifier][:2]
        assert result == {
            k: v for k, v in frozen[identifier][2].items() if k != "$archive_document"
        }
    assert purge(client, document["id"], "cleanup-archive").json() == purge_result
    assert archive_status(client, document["id"], "cleanup-archive").json()["state"] == "purged"
    assert restore(client, document["id"], "cleanup-archive").status_code == 410
    assert (
        client.get("/api/v1/canvas-write-receipts/cleanup-archive").json()["result"]
        == archive_result
    )
    assert client.get(CATALOG).json()["items"] == []
    assert client.get(path).status_code == 404
    assert cleanup(resource_app)["examined"] == 0
    with resource_app[1]() as session:
        assert session.get(MediaFile, int(resource["id"])) is None
        assert session.get(CanvasBinaryResource, binary_id) is None
        jobs = list(session.scalars(select(CanvasResourceDeletion)))
        assert len(jobs) == 2 and all(row.status == "pending" for row in jobs)
        locators = [row.storage_locator for row in jobs]
        assert session.scalar(select(func.count()).select_from(CanvasResourceUpload)) == 0
    storage = resource_app[0].state.storage
    original, removed = storage.remove, []

    def lose_first_response(bucket, name):
        original(bucket, name)
        removed.append((bucket, name))
        if len(removed) == 1:
            raise StorageUnavailable("测试：删除字节后响应丢失")

    with monkeypatch.context() as fault:
        fault.setattr(storage, "remove", lose_first_response)
        first = remove_bytes(resource_app)
    assert first["failed"] == 1 and first["completed"] == 1
    with resource_app[1].begin() as session:
        pending = session.scalar(
            select(CanvasResourceDeletion).where(CanvasResourceDeletion.status == "pending")
        )
        assert pending is not None and pending.attempts == 1
        pending.next_attempt_at = utcnow() - timedelta(seconds=1)
    assert remove_bytes(resource_app)["completed"] == 1
    assert remove_bytes(resource_app)["examined"] == 0
    for locator in locators:
        settings = resource_app[2]
        location = ObjectLocation.parse(
            locator,
            {settings.minio_image_bucket, settings.minio_video_bucket, settings.minio_audio_bucket},
        )
        with pytest.raises(NotFound):
            storage.stat(location.bucket, location.object_name)
    assert upload(client, png(), key="image-upload", source=document["id"]).status_code == 404
    assert not database_errors


def test_other_canvas_and_owner_private_library_keep_original_media(resource_app, database_errors):
    owner, project, path, document, _, _, resource = history_scene(resource_app, "purge_refs")
    outsider, _ = account(resource_app, "purge_refs_outside")
    sibling = owner.post(
        f"/api/v1/projects/{project['id']}/canvases",
        headers={"Idempotency-Key": "purge-sibling"},
        json={"title": "保留引用作品", "source_key": "purge-sibling"},
    )
    assert sibling.status_code == 201, sibling.text
    sibling_path = f"/api/v1/projects/{project['id']}/canvases/{sibling.json()['id']}"
    sibling_document = owner.get(sibling_path + "/my-document").json()["source_document"]
    sibling_document["nodes"] = [image_node(resource)]
    expected = save(owner, sibling_path, sibling_document, "purge-sibling-save")
    private = upload(owner, png("blue"), key="purge-library-media", source=document["id"])
    assert private.status_code == 200, private.text
    private_resource = private.json()["resource"]
    document["nodes"].append(image_node(private_resource))
    document = save(owner, path, document, "purge-library-source-publish")
    # 所有者私人素材与另一个活动画布均能保留仍在使用的源媒体。
    register(owner, "owner-library", private_resource)
    assert outsider.get(ASSETS + "/owner-library").status_code == 404
    dispose(resource_app, owner, path, document)
    result = cleanup(resource_app)
    assert result["completed"] == 1 and result["retained_uploads"] == 2
    assert result["retired_uploads"] == 0 and result["failed"] == 0
    assert remove_bytes(resource_app)["examined"] == 0
    assert owner.get(sibling_path + "/my-document").json()["source_document"] == expected
    assert owner.get(f"{ROOT}/{resource['id']}/file").content == png()
    assert owner.get(f"{ROOT}/{private_resource['id']}/file").content == png("blue")
    assert outsider.get(f"{ROOT}/{private_resource['id']}/file").status_code == 404
    assert owner.get(ASSETS + "/owner-library").status_code == 200
    assert not database_errors


def test_another_accounts_canonical_locator_protects_bytes_after_retirement(resource_app):
    client, _, path, document, _, _, resource = history_scene(resource_app, "purge_locator")
    other, other_user = account(resource_app, "purge_locator_other")
    with resource_app[1].begin() as session:
        source = session.get(MediaFile, int(resource["id"]))
        identifier = next_id()
        session.add(
            CanvasBinaryResource(
                id=identifier,
                scope_user_id=int(other_user["id"]),
                project_id=None,
                published_at=None,
                resource_kind="file",
                mime_type="image/png",
                storage_locator=source.storage_locator,
                original_name="retained.png",
                byte_size=source.byte_size,
                checksum_sha256=source.checksum_sha256,
                created_at=utcnow(),
                updated_at=utcnow(),
                created_by=int(other_user["id"]),
                updated_by=int(other_user["id"]),
            )
        )
    dispose(resource_app, client, path, document)
    assert cleanup(resource_app)["retired_uploads"] == 1
    assert remove_bytes(resource_app)["retained"] == 1
    assert other.get(f"{ROOT}/{identifier}/file").content == png()
    assert client.get(f"{ROOT}/{identifier}/file").status_code == 404


def test_failed_payload_transaction_preserves_graph_frozen_preview_and_uploads(
    resource_app, monkeypatch
):
    client, project, path, document, _, _, _ = history_scene(resource_app, "purge_rollback_payload")
    dispose(resource_app, client, path, document)
    canvas_id = int(project["primary_canvas_id"])
    before, frozen = payload_counts(resource_app, canvas_id), receipts(resource_app, canvas_id)
    original = CanvasRecycleCleanupDAO.remove_payload

    def fail_after_deletes(dao, identifier):
        original(dao, identifier)
        raise RuntimeError("测试：图载荷删除事务失败")

    with monkeypatch.context() as fault:
        fault.setattr(CanvasRecycleCleanupDAO, "remove_payload", fail_after_deletes)
        result = cleanup(resource_app)
    assert result["failed"] == 1 and result["completed"] == 0
    assert payload_counts(resource_app, canvas_id) == before
    assert receipts(resource_app, canvas_id) == frozen
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasResourceUpload)) == 1
        assert session.scalar(select(func.count()).select_from(CanvasResourceDeletion)) == 0
    assert cleanup(resource_app)["completed"] == 1


def test_failed_commit_restores_retired_canonical_resource_and_deletion_outbox(resource_app):
    client, project, path, document, _, _, resource = history_scene(resource_app, "purge_commit")
    dispose(resource_app, client, path, document)
    canvas_id = int(project["primary_canvas_id"])
    before, frozen = payload_counts(resource_app, canvas_id), receipts(resource_app, canvas_id)

    def fail_commit(session):
        # 真实DELETE、队列INSERT和完成标记均已flush，提交前模拟数据库失败。
        assert session.get(MediaFile, int(resource["id"])) is None
        assert session.scalar(select(func.count()).select_from(CanvasResourceDeletion)) == 1
        raise RuntimeError("测试：回收事务提交失败")

    event.listen(resource_app[1].class_, "before_commit", fail_commit)
    try:
        result = cleanup(resource_app)
    finally:
        event.remove(resource_app[1].class_, "before_commit", fail_commit)
    assert result["failed"] == 1 and result["completed"] == 0
    assert payload_counts(resource_app, canvas_id) == before
    assert receipts(resource_app, canvas_id) == frozen
    with resource_app[1]() as session:
        assert session.get(MediaFile, int(resource["id"])) is not None
        assert session.scalar(select(func.count()).select_from(CanvasResourceUpload)) == 1
        assert session.scalar(select(func.count()).select_from(CanvasResourceDeletion)) == 0
    assert cleanup(resource_app)["completed"] == 1
    assert remove_bytes(resource_app)["completed"] == 1


@pytest.mark.parametrize("state", ["queued", "sent", "unknown", "tool", "local", "render"])
def test_execution_or_unknown_evidence_defers_payload_until_authoritative_release(
    resource_app, state
):
    client, user, project, path, request = seed(resource_app, "purge_guard_" + state)
    task = admit(client, request)
    document = client.get(path + "/my-document").json()["source_document"]
    job_id = None
    if state == "local":
        media = upload(client, png(), key="guard-input", source=document["id"])
        assert media.status_code == 200, media.text
    with resource_app[1].begin() as session:
        async_task = session.get(AsyncTask, int(task["id"]))
        record = session.scalar(select(AIGenerationRecord))
        if state != "queued":
            finish(async_task, "cancelled")
            record.status = state if state in {"sent", "unknown"} else "failed"
        if state in {"tool", "local", "render"}:
            job_id = next_id()
            values = {
                "id": job_id,
                "created_at": utcnow(),
                "updated_at": utcnow(),
                "created_by": int(user["id"]),
                "updated_by": int(user["id"]),
                "project_id": int(project["id"]),
                "canvas_id": int(project["primary_canvas_id"]),
                "user_id": int(user["id"]),
                "source_snapshot": {"fixture": "execution guard"},
                "request_hash": "0" * 64,
            }
            if state == "tool":
                values.pop("request_hash")
                session.add(
                    CanvasToolRun(
                        **values,
                        client_key="guard-tool",
                        create_hash="0" * 64,
                        state_json={},
                        source_hash="0" * 64,
                        model_config_id=record.config_id,
                        config_hash="0" * 64,
                        execution_owner="guard-executor",
                        execution_epoch=1,
                        lease_expires_at=utcnow() + timedelta(minutes=1),
                    )
                )
            else:
                values.update(
                    client_operation_id="guard-job",
                    request_json={},
                    canvas_version=int(document["revision"]),
                    status="running",
                    lease_token="0" * 32,
                    locked_until=utcnow() + timedelta(minutes=1),
                    next_run_at=utcnow(),
                )
                if state == "render":
                    session.add(CanvasRenderJob(**values, plan_json={}))
                else:
                    # 输入沿用实际上传，不执行本地Worker；这里只构造持久运行状态。
                    session.add(
                        CanvasLocalJob(
                            **values,
                            task_type="depth_capture",
                            source_media_id=int(media.json()["resource"]["id"]),
                        )
                    )
    dispose(resource_app, client, path, document)
    canvas_id = int(project["primary_canvas_id"])
    before, frozen = payload_counts(resource_app, canvas_id), receipts(resource_app, canvas_id)
    assert cleanup(resource_app)["deferred"] == 1
    assert payload_counts(resource_app, canvas_id) == before
    assert receipts(resource_app, canvas_id) == frozen
    with resource_app[1].begin() as session:
        finish(session.get(AsyncTask, int(task["id"])), "cancelled")
        session.scalar(select(AIGenerationRecord)).status = "failed"
        if state == "tool":
            job = session.get(CanvasToolRun, job_id)
            job.execution_owner, job.lease_expires_at = "", None
        elif state in {"local", "render"}:
            job = session.get(CanvasLocalJob if state == "local" else CanvasRenderJob, job_id)
            job.status, job.lease_token, job.locked_until = "cancelled", None, None
            job.finished_at = utcnow()
    assert cleanup(resource_app)["completed"] == 1
    assert not any(payload_counts(resource_app, canvas_id).values())
    with resource_app[1]() as session:
        assert session.get(AsyncTask, int(task["id"])) is not None
        assert session.scalar(select(AIGenerationRecord)) is not None


def test_two_cleanup_processes_commit_one_retirement(resource_app, monkeypatch, database_errors):
    client, _, path, document, _, _, _ = history_scene(resource_app, "purge_cleanup_race")
    dispose(resource_app, client, path, document)
    barrier, original = Barrier(2), CanvasRecycleCleanupDAO.candidates

    def synchronize_candidates(dao, after_id):
        rows = original(dao, after_id)
        if after_id == 0:
            barrier.wait(timeout=10)
        return rows

    with monkeypatch.context() as fault:
        fault.setattr(CanvasRecycleCleanupDAO, "candidates", synchronize_candidates)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: cleanup(resource_app), range(2)))
    assert sum(result["completed"] for result in results) == 1
    assert sum(result["retired_uploads"] for result in results) == 1
    assert sum(result["failed"] for result in results) == 0
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasResourceDeletion)) == 1
    assert not database_errors


def test_actor_session_cannot_invoke_internal_cleanup(resource_app):
    client, user = account(resource_app, "purge_maintenance_actor")

    def actor_factory():
        return resource_app[1](info={"actor": actor(int(user["id"]))})

    with pytest.raises(ValueError, match="internal maintenance"):
        cleanup_canvas_recycle(actor_factory, resource_app[2])
    assert client.get(CATALOG).json()["items"] == []


def test_one_hundred_deferred_receipts_do_not_starve_later_graphs_and_media(resource_app):
    owner, user = account(resource_app, "purge_pages_owner")
    source = upload(owner, png(), key="purge-pages-private-input")
    assert source.status_code == 200, source.text
    source_id = int(source.json()["resource"]["id"])
    later, jobs = [], []
    for index in range(102):
        key = f"purge-page-{index}"
        project, path = create_canvas(owner, key)
        document = owner.get(path + "/my-document").json()["source_document"]
        if index < 100:
            now, identifier = utcnow(), next_id()
            with resource_app[1].begin() as session:
                session.add(
                    CanvasLocalJob(
                        id=identifier,
                        created_at=now,
                        updated_at=now,
                        created_by=int(user["id"]),
                        updated_by=int(user["id"]),
                        user_id=int(user["id"]),
                        project_id=int(project["id"]),
                        canvas_id=int(project["primary_canvas_id"]),
                        task_type="depth_capture",
                        client_operation_id=key,
                        request_hash="0" * 64,
                        request_json={"fixture": "deferred cleanup page"},
                        source_snapshot={"resource_id": str(source_id)},
                        source_media_id=source_id,
                        canvas_version=int(document["revision"]),
                        status="queued",
                        next_run_at=now + timedelta(days=1),
                    )
                )
            jobs.append(identifier)
        else:
            response = upload(owner, png("blue"), key=key + "-image", source=document["id"])
            assert response.status_code == 200, response.text
            resource = response.json()["resource"]
            document["nodes"] = [image_node(resource)]
            document = save(owner, path, document, key + "-save")
            later.append((int(project["primary_canvas_id"]), int(resource["id"])))
        archive(owner, path, document, key + "-archive")
        response = purge(owner, document["id"], key + "-archive", key + "-purge")
        assert response.status_code == 200, response.text
    result = cleanup(resource_app)
    assert result == {
        "examined": 102,
        "completed": 2,
        "deferred": 100,
        "retired_uploads": 2,
        "retained_uploads": 0,
        "retired_creation_resources": 0,
        "retained_creation_resources": 0,
        "failed": 0,
        "applied": True,
    }
    for canvas_id, _ in later:
        assert not any(payload_counts(resource_app, canvas_id).values())
    assert remove_bytes(resource_app)["completed"] == 2
    assert owner.get(f"{ROOT}/{source_id}/file").content == png()
    with resource_app[1]() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(CanvasLocalJob)
                .where(CanvasLocalJob.id.in_(jobs), CanvasLocalJob.status == "queued")
            )
            == 100
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(MediaFile)
                .where(MediaFile.id.in_([identifier for _, identifier in later]))
            )
            == 0
        )
