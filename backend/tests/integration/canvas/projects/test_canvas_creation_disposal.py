"""本人处置后的创建请求回收；真实 HTTP/MySQL/MinIO，不调用供应商。"""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select

from short_drama.core.exceptions import NotFound
from short_drama.dao.canvas_creation_dao import CanvasCreationDAO
from short_drama.dao.canvas_recycle_cleanup_dao import CanvasRecycleCleanupDAO
from short_drama.domain import (
    CanvasCreationAttempt,
    CanvasCreationResource,
    CanvasResourceDeletion,
    CanvasResourceUpload,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.canvas import CanvasWriteReceipt
from short_drama.service.canvas_creation_service import CanvasCreationService
from tests.integration.canvas.document.test_canvas_workspace import actor
from tests.integration.canvas.projects.test_canvas_creation import creation_request
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_recycle import archive, restore
from tests.integration.canvas.resources.test_canvas_recycle_catalog import purge
from tests.integration.canvas.resources.test_canvas_recycle_cleanup import (
    cleanup,
    payload_counts,
    remove_bytes,
)
from tests.integration.canvas.resources.test_canvas_resource_copy import register
from tests.integration.canvas.resources.test_canvas_resources import (
    ROOT,
    account,
    canvas,
    png,
    upload,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="创建请求处置需显式启用隔离 MinIO",
    ),
]


def scene(
    app,
    name,
    *,
    existing=False,
    with_media=True,
    with_library=False,
    client=None,
    user=None,
    project=None,
    original=None,
):
    if client is None:
        client, user = account(app, name)
    if original is None:
        original = upload(client, png(), key=name + "-upload").json()["resource"]
    if with_library:
        register(client, "creation-original", original)
    payload = creation_request(original, name)
    payload["source_document"]["nodes"].append(
        {
            "id": "private-input",
            "type": "text",
            "title": "创建正文",
            "position": {"x": 500, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"content": "原始创建正文", "prompt": "仅本人创建参数"},
        }
    )
    if not with_media:
        payload["source_document"]["nodes"].pop(0)
    route = "/api/v1/canvas-workspace"
    if existing:
        if project is None:
            project, _, _ = canvas(client, name + "-host")
        route = f"/api/v1/projects/{project['id']}/canvases"
        payload["source_document"]["workspaceProjectId"] = project["id"]
    headers = {"Idempotency-Key": name + "-create"}
    response = client.post(route, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    path = f"/api/v1/projects/{result['project_id']}/canvases/{result['id']}"
    with app[1]() as session:
        attempt = session.scalar(
            select(CanvasCreationAttempt).where(
                CanvasCreationAttempt.idempotency_key == headers["Idempotency-Key"],
                CanvasCreationAttempt.user_id == int(user["id"]),
            )
        )
        frozen = {
            column.key: deepcopy(getattr(attempt, column.key))
            for column in CanvasCreationAttempt.__table__.columns
        }
        prepared = {
            row.id: {
                column.key: deepcopy(getattr(row, column.key))
                for column in CanvasCreationResource.__table__.columns
            }
            for row in session.scalars(
                select(CanvasCreationResource).where(
                    CanvasCreationResource.attempt_id == attempt.id
                )
            )
        }
    return SimpleNamespace(
        client=client,
        user=user,
        original=original,
        payload=payload,
        headers=headers,
        route=route,
        result=result,
        path=path,
        frozen=frozen,
        prepared=prepared,
    )


def dispose(item, *, client=None):
    client = client or item.client
    document = client.get(item.path + "/my-document").json()["source_document"]
    key = item.payload["source_key"] + "-archive"
    archive(client, item.path, document, key)
    response = purge(client, document["id"], key, key + "-purge")
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("existing,with_media", [(False, True), (True, True), (False, False)])
def test_disposal_erases_the_original_creation_body_and_refuses_replay(
    resource_app, existing, with_media
):
    item = scene(
        resource_app,
        f"cr_request_{existing}_{with_media}",
        existing=existing,
        with_media=with_media,
    )
    dispose(item)
    assert cleanup(resource_app, apply=False)["examined"] == 1
    with resource_app[1]() as session:
        assert (
            session.get(CanvasCreationAttempt, item.frozen["id"]).request_json
            == item.frozen["request_json"]
        )
    result = cleanup(resource_app)
    assert result["completed"] == 1 and result["failed"] == 0
    with resource_app[1]() as session:
        attempt = session.get(CanvasCreationAttempt, item.frozen["id"])
        assert attempt.request_json == {}, "已处置作品仍保留完整创建正文和私人输入"
        for field, value in item.frozen.items():
            if field not in {"request_json", "updated_at"}:
                assert getattr(attempt, field) == value
        for identifier, frozen in item.prepared.items():
            row = session.get(CanvasCreationResource, identifier)
            assert {key: deepcopy(getattr(row, key)) for key in frozen} == frozen
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.actor_user_id == int(item.user["id"]),
                CanvasWriteReceipt.idempotency_key == item.headers["Idempotency-Key"],
            )
        )
        assert receipt.result_json == item.result and receipt.request_hash == attempt.request_hash
        if with_media:
            assert (
                session.get(MediaFile, int(item.result["resource_map"][item.original["id"]]))
                is None
            )
    replay = item.client.post(item.route, headers=item.headers, json=item.payload)
    assert replay.status_code == 404, replay.text
    assert stored_request(resource_app, item) == {}
    changed = item.client.post(
        item.route, headers=item.headers, json={**item.payload, "title": "同键更换正文"}
    )
    assert changed.status_code == 409, changed.text
    assert item.client.get(item.path).status_code == 404
    assert remove_bytes(resource_app)["failed"] == 0
    assert item.client.get(f"{ROOT}/{item.original['id']}/file").content == png()
    assert cleanup(resource_app)["examined"] == 0


def stored_request(app, item):
    with app[1]() as session:
        return deepcopy(session.get(CanvasCreationAttempt, item.frozen["id"]).request_json)


def keep_active(item):
    result = item.client.post(
        f"/api/v1/projects/{item.result['project_id']}/canvases",
        headers={"Idempotency-Key": item.payload["source_key"] + "-sibling"},
        json={"title": "保留活动画布", "source_key": item.payload["source_key"] + "-sibling"},
    )
    assert result.status_code == 201, result.text


@pytest.mark.parametrize("restored", [False, True])
def test_archive_without_disposal_and_restored_work_keep_the_original_request(
    resource_app, restored
):
    item = scene(resource_app, f"cr_archive_{restored}")
    document = item.client.get(item.path + "/my-document").json()["source_document"]
    archive(item.client, item.path, document, "creation-archive-only")
    if restored:
        response = restore(item.client, document["id"], "creation-archive-only")
        assert response.status_code == 200, response.text
    assert cleanup(resource_app)["examined"] == 0
    assert stored_request(resource_app, item) == item.frozen["request_json"]


def test_owner_disposal_preserves_another_accounts_creation_and_media(resource_app):
    owned = scene(resource_app, "cr_scope_owner")
    member, user = account(resource_app, "cr_scope_member")
    project, _, _ = canvas(member, "cr-scope-host")
    item = scene(
        resource_app, "cr-scope-work", existing=True, client=member, user=user, project=project
    )
    assert owned.client.get(item.path).status_code == 404
    dispose(owned)
    result = cleanup(resource_app)
    assert result["completed"] == 1
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    with resource_app[1]() as session:
        copied = int(item.result["resource_map"][item.original["id"]])
        assert session.get(MediaFile, copied) is not None
        assert (
            session.scalar(
                select(CanvasResourceUpload).where(
                    CanvasResourceUpload.reserved_resource_id == copied
                )
            )
            is not None
        )
    assert member.post(item.route, headers=item.headers, json=item.payload).json() == item.result


def test_independent_library_protects_media_without_retaining_the_creation_body(resource_app):
    item = scene(resource_app, "cr_library_kept", with_library=True)
    copied = int(item.result["resource_map"][item.original["id"]])
    dispose(item)
    result = cleanup(resource_app)
    assert result["completed"] == result["retained_uploads"] == 1
    assert result["retired_uploads"] == 0 and stored_request(resource_app, item) == {}
    with resource_app[1]() as session:
        assert session.get(MediaFile, copied) is not None
        assert session.scalar(select(CanvasResourceDeletion)) is None
    with resource_app[0].state.storage.open(
        resource_app[2].minio_image_bucket, f"canvas/resources/{copied}"
    ) as response:
        assert response.read() == png()


def test_descendant_copy_keeps_frozen_aliases_after_the_first_work_is_reclaimed(resource_app):
    first = scene(resource_app, "cr_lineage_first")
    intermediate = {**first.original, "id": first.result["resource_map"][first.original["id"]]}
    second = scene(
        resource_app,
        "cr_lineage_second",
        client=first.client,
        user=first.user,
        original=intermediate,
    )
    last = second.result["resource_map"][intermediate["id"]]
    assert set(second.result["resource_aliases"][last]) == {
        first.original["id"],
        intermediate["id"],
    }
    dispose(first)
    assert cleanup(resource_app)["completed"] == 1
    assert stored_request(resource_app, first) == {}
    assert stored_request(resource_app, second) == second.frozen["request_json"]
    assert remove_bytes(resource_app)["completed"] == 1
    with pytest.raises(NotFound):
        resource_app[0].state.storage.stat(
            resource_app[2].minio_image_bucket, "canvas/resources/" + intermediate["id"]
        )
    normalized = second.client.post(
        ROOT + "/normalize",
        json={
            "canvas_key": second.result["source_key"],
            "resource_ids": [intermediate["id"]],
        },
    )
    assert normalized.status_code == 200, normalized.text
    assert normalized.json()["resource_map"] == {intermediate["id"]: last}
    assert set(normalized.json()["resource_aliases"][last]) == {
        first.original["id"],
        intermediate["id"],
    }
    assert second.client.get(f"{ROOT}/{last}/file").content == png()
    assert (
        second.client.post(second.route, headers=second.headers, json=second.payload).json()
        == second.result
    )


def test_prior_v2_graph_cleanup_is_revisited_only_for_its_remaining_creation_body(resource_app):
    item = scene(resource_app, "cr_legacy_cleaned", with_media=False)
    dispose(item)
    assert cleanup(resource_app)["completed"] == 1
    with resource_app[1].begin() as session:
        # 模拟旧版已清图、尚未擦除创建正文的实际持久状态。
        session.get(CanvasCreationAttempt, item.frozen["id"]).request_json = deepcopy(
            item.frozen["request_json"]
        )
        marker = deepcopy(session.get(ProjectCanvas, int(item.result["id"])).properties_json)
        assert marker["$recycle_cleanup"]["version"] == 2
    assert cleanup(resource_app, apply=False)["examined"] == 1
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    assert cleanup(resource_app)["completed"] == 1
    assert stored_request(resource_app, item) == {}
    with resource_app[1]() as session:
        assert session.get(ProjectCanvas, int(item.result["id"])).properties_json == marker
    assert cleanup(resource_app)["examined"] == 0


def test_creation_body_and_media_retirement_roll_back_with_failed_graph_commit(resource_app):
    item = scene(resource_app, "cr_request_rollback")
    dispose(item)
    before = payload_counts(resource_app, int(item.result["id"]))
    observed = []

    def fail_commit(session):
        assert session.get(CanvasCreationAttempt, item.frozen["id"]).request_json == {}
        assert session.scalar(select(CanvasResourceDeletion)) is not None
        observed.append(True)
        raise RuntimeError("测试：创建正文擦除和媒体退役后提交失败")

    event.listen(resource_app[1].class_, "before_commit", fail_commit)
    try:
        result = cleanup(resource_app)
    finally:
        event.remove(resource_app[1].class_, "before_commit", fail_commit)
    assert observed == [True] and result["failed"] == 1 and result["completed"] == 0
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    assert payload_counts(resource_app, int(item.result["id"])) == before
    with resource_app[1]() as session:
        assert session.scalar(select(CanvasResourceDeletion)) is None
        assert (
            session.get(MediaFile, int(item.result["resource_map"][item.original["id"]]))
            is not None
        )
    assert cleanup(resource_app)["completed"] == 1 and stored_request(resource_app, item) == {}


@pytest.mark.parametrize("field", ["id", "project_id", "source_key"])
def test_mismatched_frozen_target_is_never_erased_by_project_or_source_guessing(
    resource_app, field
):
    item = scene(resource_app, "cr_mismatch_" + field)
    dispose(item)
    with resource_app[1].begin() as session:
        attempt = session.get(CanvasCreationAttempt, item.frozen["id"])
        result = deepcopy(attempt.result_json)
        result[field] = "another-work" if field == "source_key" else str(int(result[field]) + 1)
        attempt.result_json = result
    assert cleanup(resource_app)["completed"] == 1
    assert stored_request(resource_app, item) == item.frozen["request_json"]


@pytest.mark.parametrize("failure", ["missing", "hash", "result"])
def test_public_creation_receipt_is_required_and_inconsistency_preserves_all_payloads(
    resource_app, failure
):
    item = scene(resource_app, "cr_receipt_" + failure)
    dispose(item)
    before = payload_counts(resource_app, int(item.result["id"]))
    with resource_app[1].begin() as session:
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.actor_user_id == int(item.user["id"]),
                CanvasWriteReceipt.idempotency_key == item.headers["Idempotency-Key"],
            )
        )
        if failure == "missing":
            session.delete(receipt)
        elif failure == "hash":
            receipt.request_hash = "f" * 64
        else:
            receipt.result_json = {**receipt.result_json, "title": "损坏的公开结果"}
    result = cleanup(resource_app)
    assert result["failed"] == 1 and result["completed"] == 0
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    assert payload_counts(resource_app, int(item.result["id"])) == before
    with resource_app[1]() as session:
        assert (
            session.get(MediaFile, int(item.result["resource_map"][item.original["id"]]))
            is not None
        )
        assert session.scalar(select(CanvasResourceDeletion)) is None


def test_unfinished_source_pin_in_a_ready_attempt_defers_body_and_graph_cleanup(resource_app):
    item = scene(resource_app, "cr_pin_not_released")
    dispose(item)
    with resource_app[1].begin() as session:
        row = session.get(CanvasCreationResource, next(iter(item.prepared)))
        row.status = "pending"
        row.copied_at = row.released_at = None
        row.source_media_id = int(item.original["id"])
    result = cleanup(resource_app)
    assert result["failed"] == 1 and result["completed"] == 0
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    with resource_app[1]() as session:
        assert session.get(MediaFile, int(item.original["id"])) is not None
        assert (
            session.get(MediaFile, int(item.result["resource_map"][item.original["id"]]))
            is not None
        )


def test_actor_session_cannot_invoke_creation_request_maintenance(resource_app):
    item = scene(resource_app, "cr_actor_maintenance")
    dispose(item)
    with resource_app[1](info={"actor": actor(int(item.user["id"]))}) as session:
        with pytest.raises(ValueError, match="internal maintenance"):
            CanvasCreationDAO(session).clear_disposed(None, None)
    assert stored_request(resource_app, item) == item.frozen["request_json"]


def test_two_cleanup_processes_clear_the_same_creation_once(
    resource_app, monkeypatch, database_errors
):
    item = scene(resource_app, "cr_concurrent_clean")
    dispose(item)
    barrier, candidates = Barrier(2), CanvasRecycleCleanupDAO.candidates

    def synchronized(dao, after_id):
        result = candidates(dao, after_id)
        if after_id == 0:
            assert result
            barrier.wait(10)
        return result

    with monkeypatch.context() as control:
        control.setattr(CanvasRecycleCleanupDAO, "candidates", synchronized)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: cleanup(resource_app), range(2)))
    assert sum(result["completed"] for result in results) == 1
    assert sum(result["failed"] for result in results) == 0
    assert stored_request(resource_app, item) == {} and database_errors == []
    with resource_app[1]() as session:
        assert len(list(session.scalars(select(CanvasResourceDeletion)))) == 1


def test_ready_receipt_replay_during_cleanup_is_refused_without_restoring_body_or_media(
    resource_app, monkeypatch, database_errors
):
    item = scene(resource_app, "cr_replay_during_cleanup")
    dispose(item)
    entered, replay_entered, finish = Event(), Event(), Event()
    reserve = CanvasCreationService._reserve_creation

    def mark_replay(service, *args, **options):
        replay_entered.set()
        return reserve(service, *args, **options)

    def pause_commit(session):
        if (
            not session.info.get("actor")
            and session.get(CanvasCreationAttempt, item.frozen["id"]).request_json == {}
        ):
            entered.set()
            assert finish.wait(10)

    event.listen(resource_app[1].class_, "before_commit", pause_commit)
    try:
        with monkeypatch.context() as control:
            control.setattr(CanvasCreationService, "_reserve_creation", mark_replay)
            with ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(cleanup, resource_app)
                try:
                    assert entered.wait(10)
                    replay = pool.submit(
                        item.client.post, item.route, headers=item.headers, json=item.payload
                    )
                    assert replay_entered.wait(10)
                finally:
                    finish.set()
                assert future.result(timeout=15)["completed"] == 1
                response = replay.result(timeout=15)
                assert response.status_code == 404, response.text
    finally:
        event.remove(resource_app[1].class_, "before_commit", pause_commit)
    assert stored_request(resource_app, item) == {} and database_errors == []
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(ProjectCanvas)) == 1
        assert (
            session.scalar(
                select(CanvasResourceUpload).where(
                    CanvasResourceUpload.reserved_resource_id
                    == int(item.result["resource_map"][item.original["id"]])
                )
            )
            is None
        )


def test_missing_receipt_ready_fallback_and_cleanup_share_project_first_lock_order(
    resource_app, monkeypatch, database_errors
):
    item = scene(resource_app, "cr_fallback_lock")
    keep_active(item)
    dispose(item)
    with resource_app[1].begin() as session:
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.actor_user_id == int(item.user["id"]),
                CanvasWriteReceipt.idempotency_key == item.headers["Idempotency-Key"],
            )
        )
        session.delete(receipt)
    read_attempt, locked_target, continuing = Event(), Event(), Event()
    by_key, lock_target = CanvasCreationDAO.by_key, CanvasRecycleCleanupDAO.lock_target

    def paused_lookup(dao, user_id, key, **options):
        result = by_key(dao, user_id, key, **options)
        if key == item.headers["Idempotency-Key"]:
            read_attempt.set()
            assert locked_target.wait(10)
            continuing.set()
        return result

    def paused_target(dao, *args, **options):
        result = lock_target(dao, *args, **options)
        if result:
            locked_target.set()
            assert continuing.wait(10)
        return result

    with monkeypatch.context() as control:
        control.setattr(CanvasCreationDAO, "by_key", paused_lookup)
        control.setattr(CanvasRecycleCleanupDAO, "lock_target", paused_target)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replay = pool.submit(
                item.client.post, item.route, headers=item.headers, json=item.payload
            )
            assert read_attempt.wait(10)
            clean = pool.submit(cleanup, resource_app)
            response, result = replay.result(timeout=15), clean.result(timeout=15)
    assert response.status_code == 404, response.text
    assert result["failed"] == 1 and result["completed"] == 0
    assert stored_request(resource_app, item) == item.frozen["request_json"]
    assert database_errors == [], "不能把 MySQL 死锁或锁超时当作正常 404"
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(ProjectCanvas)) == 2
