"""Real disposable MySQL; the provider/MinIO fixture is deterministic and local."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from test_asset_image_generation import create_asset, drain
from test_asset_image_generation import flow as flow

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.dao.task_runtime_dao import finish
from short_drama.domain import AIGenerationRecord, Asset, AsyncTask
from short_drama.service.generation_batch_service import GenerationBatchService, dispatch_batches

pytestmark = pytest.mark.integration


def setup(flow, count=3):
    flow.settings.generation_batches_enabled = True
    assets = [create_asset(flow, "character") for _ in range(count)]
    service = GenerationBatchService(flow.session, flow.settings)
    request = {
        "scene": "asset_image",
        "config_id": str(flow.config.id),
        "scope": {"library": "project", "project_id": str(flow.project.id)},
        "source_ids": [str(a.id) for a in assets],
    }
    preview = service.preflight(request)
    assert preview["task_count"] == count
    body = {
        **request,
        "preflight_hash": preview["preflight_hash"],
        "accepted_ids": request["source_ids"],
    }
    batch, created = service.create(body, str(uuid4()))
    assert created
    return service, assets, body, batch


def test_batch_holds_tasks_limits_dispatch_preserves_candidates_and_replays(flow):
    service, assets, body, batch = setup(flow)
    detail = service.detail(batch["id"])
    assert detail["counts"] == {"waiting": 3}
    task_ids = [item["task_id"] for item in detail["items"]]
    # Frozen tasks cannot be executed even if somebody delivers a fabricated message.
    flow.executor.execute(task_ids[0], 1)
    assert not flow.provider.calls
    assert dispatch_batches(flow.factory, flow.settings) == 2
    assert dispatch_batches(flow.factory, flow.settings) == 0
    assert drain(flow, task_ids[0]) == "succeeded"
    assert dispatch_batches(flow.factory, flow.settings) == 1
    assert drain(flow, task_ids[1]) == "succeeded"
    assert drain(flow, task_ids[2]) == "succeeded"
    assert service.detail(batch["id"])["status"] == "succeeded"
    assert len(flow.provider.calls) == 3
    assert all(flow.library.get(a.id).media_id is None for a in assets)
    assert all(flow.images.list(a.id)["total"] == 1 for a in assets)
    preview = service.preflight(
        {k: v for k, v in body.items() if k not in {"preflight_hash", "accepted_ids"}}
    )
    assert {i["state"] for i in preview["items"]} == {"review"}


def test_batch_preflight_conflict_and_idempotency(flow):
    flow.settings.generation_batches_enabled = True
    asset = create_asset(flow, "character")
    svc = GenerationBatchService(flow.session, flow.settings)
    request = {
        "scene": "asset_image",
        "config_id": str(flow.config.id),
        "scope": {"library": "project", "project_id": str(flow.project.id)},
        "source_ids": [str(asset.id)],
    }
    preview = svc.preflight(request)
    body = {
        **request,
        "accepted_ids": request["source_ids"],
        "preflight_hash": preview["preflight_hash"],
    }
    with flow.factory.begin() as session:
        row = session.get(Asset, asset.id)
        row.description = "changed"
        row.row_version += 1
    with pytest.raises(WorkflowError, match="预检"):
        svc.create(body, str(uuid4()))
    preview = svc.preflight(request)
    body["preflight_hash"] = preview["preflight_hash"]
    key = str(uuid4())
    original, created = svc.create(body, key)
    repeat, created_again = svc.create(body, key)
    assert created and not created_again and original["id"] == repeat["id"]
    with pytest.raises(Conflict):
        svc.create({**body, "count": 2}, key)


def test_pause_cancel_unknown_and_restart_are_durable(flow):
    svc, _, _, batch = setup(flow)
    svc.control(batch["id"], "pause")
    assert dispatch_batches(flow.factory, flow.settings) == 0
    svc.control(batch["id"], "resume")
    assert dispatch_batches(flow.factory, flow.settings) == 2
    detail = svc.detail(batch["id"])
    task_id = int(detail["items"][0]["task_id"])
    with flow.factory.begin() as session:
        task = session.get(AsyncTask, task_id)
        finish(task, "failed", {"code": "provider_acceptance_unknown"})
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        record.status = "unknown"
    assert dispatch_batches(flow.factory, flow.settings) == 0
    assert svc.detail(batch["id"])["status"] == "needs_review"
    with pytest.raises(Conflict):
        svc.control(batch["id"], "resume")
    svc.control(batch["id"], "cancel")
    result = svc.detail(batch["id"])
    assert result["counts"].get("cancelled") == 2
    assert result["status"] == "needs_review"
    assert not flow.provider.calls


def test_shared_concurrency_two_schedulers_and_retry_receipt_replay(flow):
    from concurrent.futures import ThreadPoolExecutor

    svc, _, _, batch = setup(flow, count=3)
    _, _, _, second = setup(flow, count=3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: dispatch_batches(flow.factory, flow.settings), range(2)))
    assert sum(counts) == 2
    active = [
        item
        for b in (batch, second)
        for item in svc.detail(b["id"])["items"]
        if item["status"] == "active"
    ]
    assert len(active) == 2
    failed = active[0]
    with flow.factory.begin() as session:
        task = session.get(AsyncTask, int(failed["task_id"]))
        finish(task, "failed", {"code": "provider_auth"})
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        record.status = "failed"
    with pytest.raises(WorkflowError) as error:
        flow.generations.retry(failed["task_id"], {}, str(uuid4()))
    assert error.value.code == "batch_retry_required"
    svc.detail(batch["id"])
    key = str(uuid4())

    def retry(_):
        with flow.factory() as session:
            return GenerationBatchService(session, flow.settings).retry_failed(
                batch["id"], {"item_ids": [failed["id"]]}, key
            )["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(retry, range(2)))
    assert ids[0] == ids[1]
    assert svc.detail(ids[0])["total"] == 1
    assert not flow.provider.calls


def test_waiting_source_edit_blocks_frozen_batch_and_flag_off_has_no_dispatch(flow):
    svc, assets, _, batch = setup(flow, count=1)
    with flow.factory.begin() as session:
        asset = session.get(Asset, assets[0].id)
        asset.row_version += 1
    assert dispatch_batches(flow.factory, flow.settings) == 0
    detail = svc.detail(batch["id"])
    assert detail["status"] == "paused" and detail["counts"] == {"blocked": 1}
    flow.settings.generation_batches_enabled = False
    assert (
        dispatch_batches(lambda: pytest.fail("disabled scheduler touched database"), flow.settings)
        == 0
    )
