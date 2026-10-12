"""历史创建媒体无上传身份时的真实 HTTP/MySQL/MinIO 回收。"""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from short_drama.core.exceptions import NotFound, StorageUnavailable, WorkflowError
from short_drama.dao.canvas_creation_dao import CanvasCreationDAO
from short_drama.dao.canvas_recycle_cleanup_dao import CanvasRecycleCleanupDAO
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasCreationAttempt,
    CanvasCreationResource,
    CanvasResourceCopySource,
    CanvasResourceDeletion,
    CanvasResourceUpload,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.canvas import (
    CanvasRevision,
    CanvasRevisionMediaReference,
    CanvasWriteReceipt,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas_creation_service import CanvasCreationService
from short_drama.utils.snowflake import next_id
from tests.integration.test_canvas_creation import creation_request
from tests.integration.test_canvas_creation_disposal import (
    dispose,
    keep_active,
    scene,
    stored_request,
)
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_library_deletion import ASSETS, image_node
from tests.integration.test_canvas_media_locators import save
from tests.integration.test_canvas_recycle import archive, restore
from tests.integration.test_canvas_recycle_cleanup import cleanup, payload_counts, remove_bytes
from tests.integration.test_canvas_resource_copy import copy_resource
from tests.integration.test_canvas_resources import ROOT, account, canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_workspace import actor
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.integration.test_identity_collaboration import join

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="历史创建媒体回收需显式启用隔离 MinIO",
    ),
]


def legacy_scene(app, name, **options):
    item = scene(app, name, **options)
    return without_upload(app, item)


def without_upload(app, item):
    target = int(item.result["resource_map"][item.original["id"]])
    with app[1].begin() as session:
        # 仅在随机测试库还原早期版本：创建已完成，目标及真实字节存在，无上传记录。
        upload = session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.reserved_resource_id == target)
        )
        assert upload is not None and upload.status == "ready" and upload.mode == "copy"
        session.delete(upload)
    item.target = target
    return item


def deletion(app):
    with app[1]() as session:
        return session.scalar(select(CanvasResourceDeletion))


def prior_v2(app, item):
    with app[1].begin() as session:
        disposal = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.canvas_id == int(item.result["id"]),
                CanvasWriteReceipt.operation_kind == "canvas.recycle.purge",
            )
        )
        dao = CanvasRecycleCleanupDAO(session)
        target, disposal = dao.lock_target(
            disposal.id, int(item.result["project_id"]), int(item.result["id"])
        )
        CanvasCreationDAO(session).clear_disposed(target, disposal)
        dao.remove_payload(target.id)
        target.properties_json = dao.marker(disposal.id)
    assert stored_request(app, item) == {} and deletion(app) is None


@pytest.mark.parametrize("existing", [False, True])
def test_legacy_creation_copy_is_retired_without_fabricating_an_upload(resource_app, existing):
    item = legacy_scene(resource_app, f"legacy_copy_{existing}", existing=existing)
    dispose(item)
    assert cleanup(resource_app, apply=False)["examined"] == 1
    with resource_app[1]() as session:
        assert session.get(MediaFile, item.target) is not None
        assert session.scalar(select(CanvasResourceDeletion)) is None
    result = cleanup(resource_app)
    assert result["completed"] == 1 and result["failed"] == 0
    with resource_app[1]() as session:
        assert session.get(MediaFile, item.target) is None, "无上传身份的历史创建媒体仍永久残留"
        assert (
            session.scalar(
                select(CanvasResourceUpload).where(
                    CanvasResourceUpload.reserved_resource_id == item.target
                )
            )
            is None
        )
        job = session.scalar(select(CanvasResourceDeletion))
        assert job.resource_id == item.target and job.upload_id is None
        assert job.idempotency_hash is None and job.request_hash is None and job.chunk_count == 0
        assert job.creation_resource_id == next(iter(item.prepared))
        assert (
            session.get(CanvasWriteReceipt, job.disposal_receipt_id).operation_kind
            == "canvas.recycle.purge"
        )
        for identifier, frozen in item.prepared.items():
            row = session.get(CanvasCreationResource, identifier)
            assert {field: deepcopy(getattr(row, field)) for field in frozen} == frozen
    assert (
        item.client.post(item.route, headers=item.headers, json=item.payload).json() == item.result
    )
    assert item.client.get(f"{ROOT}/{item.target}/file").status_code == 404
    assert remove_bytes(resource_app)["completed"] == 1
    with pytest.raises(NotFound):
        resource_app[0].state.storage.stat(
            resource_app[2].minio_image_bucket, f"canvas/resources/{item.target}"
        )
    assert item.client.get(f"{ROOT}/{item.original['id']}/file").content == png()
    assert cleanup(resource_app)["examined"] == 0


def test_legacy_binary_creation_uses_the_same_outbox_and_preserves_the_original(resource_app):
    client, user = account(resource_app, "legacy_binary")
    body = b"glTF-legacy-creation-copy"
    original = upload(client, body, kind="file", name="model.glb", mime="model/gltf-binary").json()[
        "resource"
    ]
    payload = creation_request(original, "legacy-binary-create")
    payload["source_document"]["nodes"] = []
    payload["source_document"]["directorScenes"] = [
        {"id": "scene", "model": f"resource:{original['id']}"}
    ]
    headers, route = {"Idempotency-Key": "legacy-binary-create"}, "/api/v1/canvas-workspace"
    response = client.post(route, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    item = without_upload(
        resource_app,
        SimpleNamespace(
            client=client,
            user=user,
            original=original,
            payload=payload,
            headers=headers,
            route=route,
            result=result,
            path=f"/api/v1/projects/{result['project_id']}/canvases/{result['id']}",
        ),
    )
    dispose(item)
    outcome = cleanup(resource_app)
    assert outcome["retired_creation_resources"] == 1 and outcome["failed"] == 0
    with resource_app[1]() as session:
        assert session.get(CanvasBinaryResource, item.target) is None
        assert session.get(MediaFile, item.target) is None
        assert session.get(CanvasBinaryResource, int(original["id"])) is not None
    assert remove_bytes(resource_app)["completed"] == 1
    with pytest.raises(NotFound):
        resource_app[0].state.storage.stat(
            resource_app[2].minio_image_bucket, f"canvas/resources/{item.target}"
        )
    assert client.get(f"{ROOT}/{original['id']}/file").content == body
    assert client.post(route, headers=headers, json=payload).json() == result


def test_prior_v2_cleanup_revisits_only_the_remaining_legacy_media(resource_app):
    item = legacy_scene(resource_app, "legacy_v2")
    dispose(item)
    prior_v2(resource_app, item)
    with resource_app[1]() as session:
        marker = deepcopy(session.get(ProjectCanvas, int(item.result["id"])).properties_json)
    assert cleanup(resource_app, apply=False)["examined"] == 1
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    with resource_app[1]() as session:
        assert session.get(ProjectCanvas, int(item.result["id"])).properties_json == marker
    assert stored_request(resource_app, item) == {}
    assert remove_bytes(resource_app)["completed"] == 1
    assert cleanup(resource_app)["examined"] == 0


@pytest.mark.parametrize("restored", [False, True])
def test_archive_without_disposal_never_reclaims_legacy_media(resource_app, restored):
    item = legacy_scene(resource_app, f"legacy_archive_{restored}")
    document = item.client.get(item.path + "/my-document").json()["source_document"]
    archive(item.client, item.path, document, "legacy-archive-only")
    if restored:
        assert restore(item.client, document["id"], "legacy-archive-only").status_code == 200
    assert cleanup(resource_app)["examined"] == 0 and deletion(resource_app) is None
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    with resource_app[1]() as session:
        assert session.get(MediaFile, item.target) is not None
    with resource_app[0].state.storage.open(
        resource_app[2].minio_image_bucket, f"canvas/resources/{item.target}"
    ) as response:
        assert response.read() == png()
    response = item.client.get(f"{ROOT}/{item.target}/file")
    assert response.status_code == (200 if restored else 404)
    if restored:
        assert response.content == png()


def test_project_owner_cannot_retire_another_authors_legacy_creation(resource_app):
    owner, _ = account(resource_app, "legacy_scope_owner")
    member, user = account(resource_app, "legacy_scope_member")
    project, _, _ = canvas(owner, "legacy-scope-host")
    join(resource_app, owner, member, project["id"], user["id"])
    item = legacy_scene(
        resource_app, "legacy_scope_work", existing=True, client=member, user=user, project=project
    )
    dispose(item, client=owner)
    result = cleanup(resource_app)
    assert result["completed"] == 1 and result["retired_creation_resources"] == 0
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    assert deletion(resource_app) is None
    assert member.get(f"{ROOT}/{item.target}/file").content == png()


def test_independent_library_protection_is_rechecked_after_its_reference_is_released(resource_app):
    item = legacy_scene(resource_app, "legacy_library_kept", with_library=True)
    keep_active(item)
    dispose(item)
    for _ in range(2):
        result = cleanup(resource_app)
        assert result["retained_creation_resources"] == 1
        assert result["retired_creation_resources"] == result["failed"] == 0
        assert stored_request(resource_app, item) == {} and deletion(resource_app) is None
    response = item.client.delete(ASSETS + "/creation-original")
    assert response.status_code == 200, response.text
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    with resource_app[1]() as session:
        jobs = {row.resource_id: row for row in session.scalars(select(CanvasResourceDeletion))}
        assert set(jobs) == {item.target, int(item.original["id"])}
        assert jobs[item.target].creation_resource_id == next(iter(item.prepared))
        assert jobs[int(item.original["id"])].upload_id is not None
    assert remove_bytes(resource_app)["completed"] == 2


@pytest.mark.parametrize("member_reference", [False, True])
def test_other_work_and_its_history_protect_legacy_media_until_their_own_disposal(
    resource_app, member_reference
):
    item = legacy_scene(resource_app, f"legacy_work_kept_{member_reference}")
    client = item.client
    if member_reference:
        client, user = account(resource_app, "legacy_work_member")
        join(resource_app, item.client, client, item.result["project_id"], user["id"])
    route = f"/api/v1/projects/{item.result['project_id']}/canvases"
    response = client.post(
        route,
        headers={"Idempotency-Key": "legacy-sibling-create"},
        json={"title": "仍在使用媒体", "source_key": "legacy-sibling"},
    )
    assert response.status_code == 201, response.text
    sibling = response.json()
    path = route + "/" + sibling["id"]
    document = client.get(path + "/my-document").json()["source_document"]
    document["nodes"] = [image_node({"id": str(item.target)})]
    document = save(client, path, document, "legacy-sibling-reference")
    dispose(item)
    assert cleanup(resource_app)["retained_creation_resources"] == 1
    with resource_app[1].begin() as session:
        # 沿用真实历史夹具：快照跨过五分钟窗口后，下一次共享提交保存前一份文档。
        for row in session.scalars(
            select(CanvasRevision).where(CanvasRevision.canvas_id == int(sibling["id"]))
        ):
            row.created_at = utcnow() - timedelta(minutes=6)
    document["nodes"] = []
    document = save(client, path, document, "legacy-sibling-release")
    with resource_app[1]() as session:
        assert (
            session.scalar(
                select(CanvasRevisionMediaReference.id).where(
                    CanvasRevisionMediaReference.canvas_id == int(sibling["id"]),
                    CanvasRevisionMediaReference.media_id == item.target,
                )
            )
            is not None
        )
    assert cleanup(resource_app)["retained_creation_resources"] == 1, "其他作品历史仍须保护媒体"
    sibling_item = SimpleNamespace(
        client=client, path=path, payload={"source_key": sibling["source_key"]}
    )
    dispose(sibling_item)
    assert cleanup(resource_app)["failed"] == 0
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    assert remove_bytes(resource_app)["completed"] == 1


def test_pending_copy_pin_protects_legacy_bytes_until_the_original_copy_finishes(
    resource_app, monkeypatch
):
    item = legacy_scene(resource_app, "legacy_copy_pin")
    keep_active(item)
    _, _, destination = canvas(item.client, "legacy-copy-pin-destination")
    resource = {**item.original, "id": str(item.target)}
    storage = resource_app[0].state.storage
    actual = storage.copy

    def lost_response(*args):
        actual(*args)
        raise StorageUnavailable("测试：复制字节成功但响应丢失")

    with monkeypatch.context() as control:
        control.setattr(storage, "copy", lost_response)
        failed = copy_resource(item.client, resource, destination["id"], key="legacy-pinned-copy")
    assert failed.status_code == 503, failed.text
    with resource_app[1]() as session:
        assert session.scalar(select(CanvasResourceCopySource)).source_media_id == item.target
    dispose(item)
    assert cleanup(resource_app)["retained_creation_resources"] == 1
    recovered = copy_resource(item.client, resource, destination["id"], key="legacy-pinned-copy")
    assert recovered.status_code == 200, recovered.text
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    assert remove_bytes(resource_app)["completed"] == 1
    assert item.client.get(f"{ROOT}/{recovered.json()['resource']['id']}/file").content == png()


@pytest.mark.parametrize("failure", ["missing", "hash", "result"])
def test_legacy_media_requires_its_unchanged_creation_receipt_even_after_prior_v2_cleanup(
    resource_app, failure
):
    item = legacy_scene(resource_app, "legacy_receipt_" + failure)
    dispose(item)
    prior_v2(resource_app, item)
    with resource_app[1].begin() as session:
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.idempotency_key == item.headers["Idempotency-Key"]
            )
        )
        if failure == "missing":
            session.delete(receipt)
        elif failure == "hash":
            receipt.request_hash = "f" * 64
        else:
            receipt.result_json = {**receipt.result_json, "title": "损坏的公开结果"}
    result = cleanup(resource_app)
    assert result["failed"] == 1 and result["completed"] == 0 and deletion(resource_app) is None
    with resource_app[1]() as session:
        assert session.get(MediaFile, item.target) is not None


@pytest.mark.parametrize("field", ["byte_size", "checksum_sha256", "storage_locator"])
def test_changed_canonical_copy_metadata_rolls_back_all_cleanup(resource_app, field):
    item = legacy_scene(resource_app, "legacy_metadata_" + field)
    dispose(item)
    counts = payload_counts(resource_app, int(item.result["id"]))
    with resource_app[1].begin() as session:
        row = session.get(MediaFile, item.target)
        changed = {
            "byte_size": row.byte_size + 1,
            "checksum_sha256": "f" * 64,
            "storage_locator": row.storage_locator + "-another-object",
        }
        setattr(row, field, changed[field])
    result = cleanup(resource_app)
    assert result["failed"] == 1 and result["completed"] == 0
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    assert payload_counts(resource_app, int(item.result["id"])) == counts
    assert deletion(resource_app) is None


def test_failed_legacy_retirement_commit_preserves_the_body_graph_record_and_bytes(resource_app):
    item = legacy_scene(resource_app, "legacy_commit_rollback")
    dispose(item)
    counts = payload_counts(resource_app, int(item.result["id"]))
    seen = []

    def fail_commit(session):
        assert session.get(CanvasCreationAttempt, item.frozen["id"]).request_json == {}
        assert session.scalar(select(CanvasResourceDeletion)).creation_resource_id
        assert session.get(MediaFile, item.target) is None
        seen.append(True)
        raise RuntimeError("测试：创建媒体退役事务提交失败")

    event.listen(resource_app[1].class_, "before_commit", fail_commit)
    try:
        result = cleanup(resource_app)
    finally:
        event.remove(resource_app[1].class_, "before_commit", fail_commit)
    assert seen == [True] and result["failed"] == 1 and result["completed"] == 0
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    assert payload_counts(resource_app, int(item.result["id"])) == counts
    assert deletion(resource_app) is None
    assert resource_app[0].state.storage.stat(
        resource_app[2].minio_image_bucket, f"canvas/resources/{item.target}"
    ).size == len(png())
    assert cleanup(resource_app)["retired_creation_resources"] == 1


def test_two_reclaimers_retire_one_legacy_copy_once(resource_app, monkeypatch, database_errors):
    item = legacy_scene(resource_app, "legacy_concurrent_cleanup")
    dispose(item)
    barrier, candidates = Barrier(2), CanvasRecycleCleanupDAO.candidates

    def together(dao, after_id):
        rows = candidates(dao, after_id)
        if after_id == 0:
            assert rows
            barrier.wait(10)
        return rows

    with monkeypatch.context() as control:
        control.setattr(CanvasRecycleCleanupDAO, "candidates", together)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: cleanup(resource_app), range(2)))
    assert sum(result["completed"] for result in results) == 1
    assert sum(result["retired_creation_resources"] for result in results) == 1
    assert sum(result["failed"] for result in results) == 0 and database_errors == []
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasResourceDeletion)) == 1


def test_ready_legacy_creation_replays_during_cleanup_without_recreating_an_upload(
    resource_app, monkeypatch, database_errors
):
    item = legacy_scene(resource_app, "legacy_replay_cleanup")
    dispose(item)
    entered, replayed, finish = Event(), Event(), Event()
    reserve = CanvasCreationService._reserve_creation

    def replay_started(service, *args, **options):
        replayed.set()
        return reserve(service, *args, **options)

    def pause_commit(session):
        if not session.info.get("actor") and session.scalar(select(CanvasResourceDeletion)):
            entered.set()
            assert finish.wait(10)

    event.listen(resource_app[1].class_, "before_commit", pause_commit)
    try:
        with monkeypatch.context() as control:
            control.setattr(CanvasCreationService, "_reserve_creation", replay_started)
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = pool.submit(cleanup, resource_app)
                try:
                    assert entered.wait(10)
                    replay = pool.submit(
                        item.client.post, item.route, headers=item.headers, json=item.payload
                    )
                    assert replayed.wait(10)
                finally:
                    finish.set()
                assert pending.result(timeout=15)["retired_creation_resources"] == 1
                response = replay.result(timeout=15)
                assert response.status_code == 201 and response.json() == item.result
    finally:
        event.remove(resource_app[1].class_, "before_commit", pause_commit)
    assert database_errors == []
    with resource_app[1]() as session:
        assert (
            session.scalar(
                select(CanvasResourceUpload).where(
                    CanvasResourceUpload.reserved_resource_id == item.target
                )
            )
            is None
        )


@pytest.mark.parametrize("lost_response", [False, True])
def test_legacy_deletion_retries_the_original_object_after_failure_or_response_loss(
    resource_app, monkeypatch, lost_response
):
    item = legacy_scene(resource_app, f"legacy_remove_{lost_response}")
    dispose(item)
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    original = deletion(resource_app)
    storage, remove, calls = resource_app[0].state.storage, resource_app[0].state.storage.remove, []

    def fail(bucket, name):
        calls.append((bucket, name))
        if lost_response:
            remove(bucket, name)
        raise StorageUnavailable("测试：对象存储删除失败或响应丢失")

    with monkeypatch.context() as control:
        control.setattr(storage, "remove", fail)
        assert remove_bytes(resource_app)["failed"] == 1
    pending = deletion(resource_app)
    assert pending.id == original.id and pending.status == "pending" and pending.attempts == 1
    assert (
        pending.storage_locator == original.storage_locator
        and pending.creation_resource_id == original.creation_resource_id
    )
    assert (
        pending.error_code == "canvas_resource_remove_failed" and pending.next_attempt_at > utcnow()
    )
    assert calls == [(resource_app[2].minio_image_bucket, f"canvas/resources/{item.target}")]
    assert remove_bytes(resource_app)["examined"] == 0
    with resource_app[1].begin() as session:
        session.get(CanvasResourceDeletion, original.id).next_attempt_at = utcnow() - timedelta(
            seconds=1
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: remove_bytes(resource_app), range(2)))
    assert sum(result["completed"] for result in results) == 1
    assert deletion(resource_app).id == original.id and deletion(resource_app).attempts == 2
    with pytest.raises(NotFound):
        storage.stat(resource_app[2].minio_image_bucket, f"canvas/resources/{item.target}")
    assert item.client.get(f"{ROOT}/{item.original['id']}/file").content == png()


@pytest.mark.parametrize("failure", ["source_map", "creation_author", "public_hash"])
def test_byte_worker_refuses_changed_creation_provenance_and_can_retry_after_repair(
    resource_app, failure
):
    item = legacy_scene(resource_app, "legacy_worker_" + failure)
    _, other = account(resource_app, "legacy_worker_other")
    dispose(item)
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    job = deletion(resource_app)
    with resource_app[1].begin() as session:
        row = session.get(CanvasCreationResource, job.creation_resource_id)
        if failure == "public_hash":
            row = session.scalar(
                select(CanvasWriteReceipt).where(
                    CanvasWriteReceipt.idempotency_key == item.headers["Idempotency-Key"]
                )
            )
            field, value = "request_hash", "f" * 64
        else:
            field = "source_resource_id" if failure == "source_map" else "created_by"
            value = row.source_resource_id + 1 if failure == "source_map" else int(other["id"])
        original, model, identifier = getattr(row, field), type(row), row.id
        setattr(row, field, value)
    assert remove_bytes(resource_app)["failed"] == 1
    assert resource_app[0].state.storage.stat(
        resource_app[2].minio_image_bucket, f"canvas/resources/{item.target}"
    ).size == len(png())
    with resource_app[1].begin() as session:
        setattr(session.get(model, identifier), field, original)
        session.get(CanvasResourceDeletion, job.id).next_attempt_at = utcnow() - timedelta(
            seconds=1
        )
    assert remove_bytes(resource_app)["completed"] == 1


@pytest.mark.parametrize("same_id", [False, True])
def test_any_accounts_canonical_record_protects_the_retired_id_or_locator(resource_app, same_id):
    item = legacy_scene(resource_app, f"legacy_canonical_{same_id}")
    _, other = account(resource_app, "legacy_canonical_other")
    dispose(item)
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    job, now = deletion(resource_app), utcnow()
    with resource_app[1].begin() as session:
        session.add(
            MediaFile(
                id=item.target if same_id else next_id(),
                scope_user_id=int(other["id"]),
                project_id=None,
                format_code="image/png",
                storage_locator=job.storage_locator + "-elsewhere"
                if same_id
                else job.storage_locator,
                original_name="retained.png",
                byte_size=len(png()),
                checksum_sha256="f" * 64,
                created_at=now,
                updated_at=now,
                created_by=int(other["id"]),
                updated_by=int(other["id"]),
            )
        )
    assert remove_bytes(resource_app)["retained"] == 1
    assert deletion(resource_app).status == "retained"
    assert resource_app[0].state.storage.stat(
        resource_app[2].minio_image_bucket, f"canvas/resources/{item.target}"
    ).size == len(png())


@pytest.mark.parametrize(
    "field",
    [
        "upload_id",
        "idempotency_hash",
        "request_hash",
        "creation_resource_id",
        "disposal_receipt_id",
        "chunk_count",
    ],
)
def test_mysql_origin_check_rejects_missing_or_mixed_proof_and_fake_chunks(resource_app, field):
    item = legacy_scene(resource_app, "lg_check_" + field)
    dispose(item)
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    job = deletion(resource_app)
    value = (
        None
        if field in {"creation_resource_id", "disposal_receipt_id"}
        else ("f" * 64 if field.endswith("hash") else 1)
    )
    with resource_app[1]() as session:
        setattr(session.get(CanvasResourceDeletion, job.id), field, value)
        with pytest.raises(OperationalError, match="3819"):
            session.flush()
        session.rollback()


def test_creation_proof_is_unique_and_actor_receipts_remain_private_and_immutable(resource_app):
    item = legacy_scene(resource_app, "legacy_receipt_guard")
    _, other = account(resource_app, "legacy_receipt_other")
    dispose(item)
    assert cleanup(resource_app)["retired_creation_resources"] == 1
    job = deletion(resource_app)
    with resource_app[1]() as session:
        clone = {column.key: deepcopy(getattr(job, column.key)) for column in job.__table__.columns}
        clone.update(id=next_id(), resource_id=next_id())
        session.add(CanvasResourceDeletion(**clone))
        with pytest.raises(IntegrityError, match="1062"):
            session.flush()
        session.rollback()
    with resource_app[1](info={"actor": actor(int(other["id"]))}) as session:
        assert session.get(CanvasResourceDeletion, job.id) is None
    with resource_app[1](info={"actor": actor(int(item.user["id"]))}) as session:
        session.get(CanvasResourceDeletion, job.id).creation_resource_id = None
        with pytest.raises(WorkflowError, match="immutable"):
            session.flush()
        session.rollback()
        with pytest.raises(ValueError, match="internal maintenance"):
            CanvasCreationDAO(session).disposed_resources(None, None)
