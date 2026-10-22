"""Asset image business flow on disposable MySQL; providers/storage are local doubles."""

import base64
from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select

from short_drama.ai import GenerationResult
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, GenerationRequestError, NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AssetVersion,
    AsyncTask,
    MediaAsset,
    MediaRecycleBin,
    ShotImage,
)
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.asset_image_service import AssetImageService
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.service.media_asset_service import MediaAssetService
from short_drama.service.project_service import ProjectService
from short_drama.service.project_storyboard_service import ProjectStoryboardService
from short_drama.service.shot_asset_service import ShotAssetService
from short_drama.service.shot_script_service import ShotScriptService
from short_drama.storage.models import ObjectLocation, StoredObject
from tests.support.integration.legacy_identity import session_factory

pytestmark = pytest.mark.integration


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    def stat(self, bucket, key):
        if (bucket, key) not in self.objects:
            raise NotFound()
        data, mime = self.objects[bucket, key]
        return StoredObject(
            bucket=bucket,
            object_name=key,
            storage_locator=ObjectLocation(bucket, key).locator,
            size=len(data),
            content_type=mime,
        )

    def put(self, bucket, key, stream, length, mime):
        data = stream.read()
        assert len(data) == length
        self.objects[bucket, key] = data, mime
        return self.stat(bucket, key)

    @contextmanager
    def open(self, bucket, key):
        data, _ = self.objects[bucket, key]
        yield SimpleNamespace(
            stream=lambda size: (data[i : i + size] for i in range(0, len(data), size))
        )

    def presigned_get(self, bucket, key, expiry):
        return f"https://media.invalid/{bucket}/{key}"

    def remove(self, bucket, key, **_):
        self.objects.pop((bucket, key), None)


class ImageProvider:
    def __init__(self):
        self.calls = []

    def validate(self, *_):
        return {}

    def submit(self, snapshot, request, *_args, **_kwargs):
        self.calls.append(deepcopy(request))
        outputs = []
        for index in range(request.get("parameters", {}).get("count", 1)):
            stream = BytesIO()
            Image.new("RGB", (4, 4), (index * 60, 80, 140)).save(stream, "PNG")
            outputs.append({"base64": base64.b64encode(stream.getvalue()).decode()})
        return GenerationResult(status="succeeded", adapter="ark_images.v1", outputs=outputs)


@pytest.fixture
def flow(mysql_engine, db_session):
    settings = Settings(_env_file=None)
    factory = session_factory(mysql_engine)
    provider, storage = ImageProvider(), MemoryStorage()
    config = AIModelConfigService(db_session).create(
        {
            "service_type": "image",
            "name": "controlled",
            "model_key": "doubao-seedream-4-5",
            "provider": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        }
    )
    project = ProjectService(db_session).create({"name": "asset flow"})
    return SimpleNamespace(
        settings=settings,
        factory=factory,
        provider=provider,
        storage=storage,
        executor=GenerationExecutionService(factory, settings, provider, storage),
        generations=AIGenerationService(db_session, settings),
        library=AssetLibraryService(db_session, settings, storage),
        images=AssetImageService(db_session, settings, storage),
        media=MediaAssetService(db_session, settings, storage),
        config=config,
        project=project,
        session=db_session,
    )


def create_asset(flow, kind="prop"):
    asset, _ = flow.library.create(
        "project",
        flow.project.id,
        flow.project.id,
        {
            "kind": kind,
            "name": f"test {kind}",
            "prompt": "blue linen",
            "description": "saved content",
        },
        str(uuid4()),
    )
    return asset


def request(flow, asset, count=1):
    return {
        "config_id": str(flow.config.id),
        "input": {"prompt": "natural light"},
        "parameters": {"count": count},
        "source": {
            "scene": "asset_image",
            "project_id": str(flow.project.id),
            "asset_id": str(asset.id),
            "row_version": str(asset.row_version),
        },
    }


def drain(flow, task_id):
    for _ in range(6):
        with flow.factory() as session:
            task = session.get(AsyncTask, int(task_id))
            if task.status in {"succeeded", "failed", "cancelled"}:
                return task.status
            version = task.message_version
        flow.executor.execute(task_id, version)
        flow.executor.execute(task_id, version)
    with flow.factory() as session:
        task = session.get(AsyncTask, int(task_id))
        pytest.fail(
            f"Controlled image task did not finish: status={task.status}, "
            f"next_action={task.next_action}, error={task.error}"
        )


def submit(flow, asset, count=1):
    task, created = flow.generations.create("image", request(flow, asset, count), str(uuid4()))
    assert created
    return task["generation_id"]


def submit_shot(flow, shot_id, count=1):
    shot = ProjectStoryboardService(flow.session).get(flow.project.id, shot_id)["shot"]
    image_settings = shot["image_settings"]
    task, created = flow.generations.create(
        "image",
        {
            "config_id": str(flow.config.id),
            "input": {"prompt": "Render the saved shot with its reference assets"},
            "parameters": {
                "count": count,
                "aspect": image_settings["aspect"],
                "resolution": image_settings["resolution"],
            },
            "source": {
                "scene": "shot_image",
                "shot_id": str(shot_id),
                "layout": image_settings["layout"],
                "context_mode": "saved",
                "row_version": shot["row_version"],
                "context_hash": shot["context_hash"],
            },
        },
        str(uuid4()),
    )
    assert created
    return task["generation_id"]


def groups(flow):
    from short_drama.service.image_group_service import ImageGroupService

    return ImageGroupService(flow.session, flow.settings, flow.storage)


def choose(flow, group, index):
    item = group["items"][index - 1]
    return groups(flow).primary(
        group["id"],
        {
            "item_id": item["id"],
            "expected_item_version": item["item_version"],
            "expected_primary_version": group["primary_version"],
            "expected_target_version": group["target_version"],
        },
    )


@pytest.mark.parametrize("failed_call", [None, 2])
def test_parallel_single_image_requests_save_fixed_group_slots(flow, monkeypatch, failed_call):
    from threading import Barrier, Lock

    from short_drama.ai import GenerationError, GenerationGateway

    barrier, lock = Barrier(4), Lock()
    calls = []
    gateway = GenerationGateway(flow.settings)

    def respond(method, url, snapshot, headers, body, **_options):
        with lock:
            calls.append(deepcopy(body))
            call_number = len(calls)
        barrier.wait(timeout=5)
        if call_number == failed_call:
            raise GenerationError("upstream_unavailable", accepted_unknown=True, http_status=503)
        stream = BytesIO()
        Image.new("RGB", (4, 4), (call_number * 40, 80, 140)).save(stream, "PNG")
        return {"data": [{"b64_json": base64.b64encode(stream.getvalue()).decode()}]}

    monkeypatch.setattr(gateway, "_json_request", respond)
    flow.executor = GenerationExecutionService(flow.factory, flow.settings, gateway, flow.storage)
    asset = create_asset(flow)
    task_id = submit(flow, asset, 4)
    assert drain(flow, task_id) == ("failed" if failed_call else "succeeded")
    current = groups(flow).current("asset", asset.id)
    assert current["status"] == ("partial" if failed_call else "completed")
    assert len(calls) == len(current["items"]) == 4
    assert all(body["sequential_image_generation"] == "disabled" for body in calls)
    expected_saved = 3 if failed_call else 4
    assert sum(item["status"] == "ready" for item in current["items"]) == expected_saved
    assert (
        len({item["media_id"] for item in current["items"] if item["media_id"]}) == expected_saved
    )
    assert len(flow.storage.objects) == expected_saved
    with flow.factory() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
        )
        response = record.response_data
        indexes = [item["output_index"] for item in response["media_manifest"]]
        assert len(indexes) == expected_saved
        assert current["primary_index"] == min(indexes)
        assert all(current["items"][index - 1]["status"] == "ready" for index in indexes)
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == expected_saved
        if failed_call:
            failures = response["image_request_errors"]
            assert len(failures) == 1
            assert failures[0]["code"] == "upstream_unavailable"
            task = session.get(AsyncTask, int(task_id))
            assert task.error["failed_output_indexes"] == [failures[0]["output_index"]]
            assert task.error["returned_count"] == expected_saved
    assert flow.generations.detail(task_id)["can_resume"] is False


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
@pytest.mark.parametrize("count", [1, 2, 4])
def test_generated_group_immediately_saves_first_primary_and_supplies_shot_references(
    flow, kind, count
):
    asset = create_asset(flow, kind)
    task_id = submit(flow, asset, count)
    pending = groups(flow).current("asset", asset.id)
    assert len(pending["items"]) == count
    assert all(item["status"] == "generating" for item in pending["items"])
    assert drain(flow, task_id) == "succeeded"
    current = groups(flow).current("asset", asset.id)
    assert current["status"] == "completed" and current["primary_index"] == 1
    saved = flow.library.get(asset.id)
    assert str(saved.media_id) == current["items"][0]["media_id"]
    assert saved.state == "confirmed" and saved.row_version == asset.row_version + 1
    assert flow.images.list(asset.id)["total"] == 0
    if count > 1:
        current = choose(flow, current, count)
    selected = current["items"][count - 1]
    with flow.factory() as session:
        versions = list(
            session.scalars(select(AssetVersion).where(AssetVersion.asset_id == asset.id))
        )
        assert len(versions) == (2 + int(count > 1) if kind == "character" else 0)
    shot = ShotScriptService(flow.session).create(
        {
            "project_id": flow.project.id,
            "position": 1,
            "script": "A figure enters.",
        }
    )
    ShotAssetService(flow.session).create(
        {
            "project_id": flow.project.id,
            "shot_id": shot.id,
            "asset_id": asset.id,
        }
    )
    shot_task = submit_shot(flow, shot.id, 2)
    assert drain(flow, shot_task) == "succeeded"
    shot_group = groups(flow).current("shot", shot.id)
    board = ProjectStoryboardService(flow.session)
    before = board.get(flow.project.id, shot.id)["shot"]
    assert before["image"]["media_id"] == shot_group["items"][0]["media_id"]
    assert not before["image"]["is_stale"]
    assert flow.provider.calls[-1]["input"]["reference_media_ids"] == [selected["media_id"]]
    choose(flow, shot_group, 2)
    after = board.get(flow.project.id, shot.id)["shot"]
    assert after["image"]["media_id"] == shot_group["items"][1]["media_id"]
    assert after["image_settings"] == before["image_settings"]
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(MediaRecycleBin)) == 1
    assert len(flow.provider.calls) == 2


def test_changed_source_preserves_paid_group_without_overwriting_and_allows_explicit_primary(flow):
    asset = create_asset(flow)
    task_id = submit(flow, asset, 2)
    changed = flow.library.patch(
        asset.id, {"row_version": str(asset.row_version), "prompt": "new input"}
    )
    assert drain(flow, task_id) == "succeeded"
    current = groups(flow).current("asset", asset.id)
    assert current["primary_index"] is None and current["notice"]
    assert flow.library.get(asset.id).media_id is None
    assert flow.provider.calls[0]["input"]["prompt"] == "natural light"
    choose(flow, current, 2)
    assert str(flow.library.get(asset.id).media_id) == current["items"][1]["media_id"]
    assert changed.prompt == "new input"


def test_deleted_asset_retains_late_group_without_overwriting_or_allowing_primary(flow):
    asset = create_asset(flow, "character")
    task_id = submit(flow, asset, 2)
    pending = groups(flow).current("asset", asset.id)
    flow.library.unlink(
        "project", flow.project.id, flow.project.id, asset.id, str(asset.row_version)
    )
    assert drain(flow, task_id) == "succeeded"
    archived = groups(flow).detail(pending["id"])
    assert archived["status"] == "completed"
    assert archived["primary_index"] is None and archived["notice"]
    assert all(item["status"] == "ready" and item["media_id"] for item in archived["items"])
    with flow.factory() as session:
        deleted = session.get(Asset, asset.id)
        assert deleted.deleted_at is not None
        assert deleted.media_id is None and deleted.row_version == asset.row_version + 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AssetVersion)
                .where(AssetVersion.asset_id == asset.id)
            )
            == 1
        )
    with pytest.raises(NotFound):
        choose(flow, archived, 1)
    with pytest.raises(NotFound):
        flow.library.get(asset.id)
    assert len(flow.provider.calls) == 1


def test_same_primary_reconfirms_changed_asset_without_regeneration(flow):
    asset = create_asset(flow, "character")
    assert drain(flow, submit(flow, asset)) == "succeeded"
    saved = flow.library.get(asset.id)
    changed = flow.library.patch(
        asset.id,
        {"row_version": str(saved.row_version), "description": "核对后沿用原图的新设定"},
    )
    assert changed.state == "unconfirmed" and changed.media_id == saved.media_id
    current = groups(flow).current("asset", asset.id)
    choose(flow, current, 1)
    confirmed = flow.library.get(asset.id)
    assert confirmed.media_id == saved.media_id and confirmed.state == "confirmed"
    assert confirmed.row_version == changed.row_version + 1
    assert confirmed.description == changed.description
    with flow.factory() as session:
        versions = list(
            session.scalars(
                select(AssetVersion)
                .where(AssetVersion.asset_id == asset.id)
                .order_by(AssetVersion.created_at, AssetVersion.id)
            )
        )
        assert len(versions) == 3
        assert versions[-1].source_row_version == changed.row_version
    assert len(flow.provider.calls) == 1


def test_same_shot_primary_refreshes_context_without_changing_generation_settings(flow):
    shot = ShotScriptService(flow.session).create(
        {"project_id": flow.project.id, "position": 1, "script": "旅人进入庭院。"}
    )
    assert drain(flow, submit_shot(flow, shot.id)) == "succeeded"
    board = ProjectStoryboardService(flow.session)
    before = board.get(flow.project.id, shot.id)["shot"]
    changed = board.update(
        flow.project.id,
        shot.id,
        {"row_version": before["row_version"], "script": "旅人走入雨夜庭院。"},
    )["shot"]
    assert not changed["image"]["is_stale"]
    with flow.factory() as session:
        previous_hash = session.scalar(
            select(ShotImage.context_hash).where(ShotImage.shot_id == shot.id)
        )
        assert previous_hash != changed["context_hash"]
    choose(flow, groups(flow).current("shot", shot.id), 1)
    confirmed = board.get(flow.project.id, shot.id)["shot"]
    assert confirmed["image"]["media_id"] == before["image"]["media_id"]
    assert not confirmed["image"]["is_stale"]
    with flow.factory() as session:
        assert (
            session.scalar(select(ShotImage.context_hash).where(ShotImage.shot_id == shot.id))
            == confirmed["context_hash"]
        )
    assert confirmed["image_settings"] == before["image_settings"]
    assert len(flow.provider.calls) == 1


def test_new_generation_retains_whole_previous_group_and_history_can_restore_primary(flow):
    asset = create_asset(flow)
    first = submit(flow, asset, 4)
    assert drain(flow, first) == "succeeded"
    old = groups(flow).current("asset", asset.id)
    second = submit(flow, flow.library.get(asset.id), 2)
    assert groups(flow).current("asset", asset.id)["expected_count"] == 2
    assert drain(flow, second) == "succeeded"
    history = groups(flow).history("asset", asset.id)
    assert history["total"] == 2
    # 历史组的目标版本必须重新读取，不能复用生成前旧版本。
    old = groups(flow).detail(old["id"])
    restored = choose(flow, old, 3)
    assert restored["is_current"] and restored["primary_index"] == 3
    assert str(flow.library.get(asset.id).media_id) == old["items"][2]["media_id"]


def test_idempotent_replay_after_edit_keeps_frozen_input_and_one_group(flow):
    from short_drama.domain import ImageGroup

    asset = create_asset(flow)
    body, key = request(flow, asset), str(uuid4())
    task, _ = flow.generations.create("image", body, key)
    changed = flow.library.patch(
        asset.id, {"row_version": str(asset.row_version), "prompt": "new green material"}
    )
    replay, created = flow.generations.create("image", body, key)
    assert not created and replay["generation_id"] == task["generation_id"]
    with pytest.raises(Conflict):
        flow.generations.create("image", request(flow, changed), key)
    assert drain(flow, task["generation_id"]) == "succeeded"
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(ImageGroup)) == 1
    assert "new green material" not in flow.provider.calls[0]["input"]["prompt"]
    filters = {"source_scene": "asset_image", "source_id": str(asset.id)}
    assert flow.generations.list(filters=filters)["total"] == 1
    assert flow.media.list(filters=filters)["total"] == 1
    other = create_asset(flow, "character")
    assert flow.generations.list(filters={**filters, "source_id": str(other.id)})["total"] == 0


def test_group_failure_rolls_back_archive_and_recovers_without_provider_call(flow, monkeypatch):
    from short_drama.service.image_group_service import ImageGroupService

    asset = create_asset(flow)
    task_id = submit(flow, asset)
    flow.executor.execute(task_id, 1)
    original = ImageGroupService.attach

    def unavailable(*_args):
        raise RuntimeError("controlled image group write failure")

    monkeypatch.setattr(ImageGroupService, "attach", unavailable)
    with flow.factory() as session:
        version = session.get(AsyncTask, int(task_id)).message_version
    flow.executor.execute(task_id, version)
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 0
    assert flow.library.get(asset.id).media_id is None
    monkeypatch.setattr(ImageGroupService, "attach", original)
    with flow.factory.begin() as session:
        session.get(AsyncTask, int(task_id)).next_run_at = utcnow()
    assert drain(flow, task_id) == "succeeded"
    assert groups(flow).current("asset", asset.id)["primary_index"] == 1
    assert len(flow.provider.calls) == 1


@pytest.mark.parametrize("saved_flag", [False, True])
@pytest.mark.parametrize("count", [1, 4])
def test_existing_output_repairs_group_without_storage_or_provider_call(
    flow, saved_flag, count, monkeypatch
):
    from short_drama.domain import ImageGroupItem

    original = flow.provider.submit

    def single_output(*args, **kwargs):
        result = original(*args, **kwargs)
        return GenerationResult(
            status=result.status, adapter=result.adapter, outputs=result.outputs[:1]
        )

    monkeypatch.setattr(flow.provider, "submit", single_output)
    asset = create_asset(flow)
    task_id = submit(flow, asset, count)
    expected_status = "succeeded" if count == 1 else "failed"
    assert drain(flow, task_id) == expected_status
    current = groups(flow).current("asset", asset.id)
    with flow.factory.begin() as session:
        session.get(ImageGroupItem, int(current["items"][0]["id"])).media_id = None
        task = session.get(AsyncTask, int(task_id))
        task.status, task.error = "failed", {"code": "archive_failed"}
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        response = deepcopy(record.response_data)
        response["media_manifest"][0].pop("image_group_saved", None)
        response["media_manifest"][0]["saved"] = saved_flag
        record.response_data = response

    def unavailable(*_args):
        raise AssertionError("Database-only archive recovery must not read storage")

    monkeypatch.setattr(flow.storage, "stat", unavailable)
    assert flow.generations.detail(task_id)["can_resume"] is True
    flow.generations.resume(task_id)
    assert drain(flow, task_id) == expected_status
    assert (
        groups(flow).detail(current["id"])["items"][0]["media_id"]
        == current["items"][0]["media_id"]
    )
    if count == 4:
        detail = flow.generations.detail(task_id)
        assert detail["can_resume"] is False
        assert "模型服务仅返回 1 个" in detail["error"]["message"]
    assert len(flow.provider.calls) == 1


@pytest.mark.parametrize("returned_count", [1, 2])
def test_partial_result_keeps_fixed_slots_and_first_successful_primary(
    flow, monkeypatch, returned_count
):
    asset = create_asset(flow)
    original = flow.provider.submit

    def fewer_outputs(*args, **kwargs):
        result = original(*args, **kwargs)
        return GenerationResult(
            status=result.status, adapter=result.adapter, outputs=result.outputs[:returned_count]
        )

    monkeypatch.setattr(flow.provider, "submit", fewer_outputs)
    task_id = submit(flow, asset, 4)
    assert drain(flow, task_id) == "failed"
    current = groups(flow).current("asset", asset.id)
    assert current["status"] == "partial" and len(current["items"]) == 4
    assert [item["status"] for item in current["items"]] == ["ready"] * returned_count + [
        "failed"
    ] * (4 - returned_count)
    assert current["primary_index"] == 1
    assert str(flow.library.get(asset.id).media_id) == current["items"][0]["media_id"]
    detail = flow.generations.detail(task_id)
    assert detail["error"]["code"] == "partial_result"
    assert f"模型服务仅返回 {returned_count} 个" in detail["error"]["message"]
    assert detail["can_resume"] is False
    with flow.factory() as session:
        task = session.get(AsyncTask, int(task_id))
        assert task.error["saved_count"] == task.error["returned_count"] == returned_count
        assert task.error["expected_count"] == 4
        assert task.error["failed_output_indexes"] == list(range(returned_count + 1, 5))
    assert len(flow.provider.calls) == 1


def test_concurrent_primary_allows_one_version_writer(flow):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    asset = create_asset(flow)
    task_id = submit(flow, asset, 4)
    assert drain(flow, task_id) == "succeeded"
    current = groups(flow).current("asset", asset.id)
    barrier = Barrier(2)

    def write(index):
        from short_drama.service.image_group_service import ImageGroupService

        with flow.factory() as session:
            item = current["items"][index - 1]
            body = {
                "item_id": item["id"],
                "expected_item_version": item["item_version"],
                "expected_primary_version": current["primary_version"],
                "expected_target_version": current["target_version"],
            }
            barrier.wait()
            try:
                return ImageGroupService(session).primary(current["id"], body)["primary_index"]
            except WorkflowError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [2, 3]))
    assert sum(isinstance(value, int) for value in results) == 1
    assert "image_version_conflict" in results


def test_queued_cancel_closes_shared_placeholders(flow):
    asset = create_asset(flow)
    task_id = submit(flow, asset, 2)
    flow.generations.cancel(task_id)
    current = groups(flow).current("asset", asset.id)
    assert current["status"] == "failed"
    assert all(item["status"] == "failed" for item in current["items"])
    assert len(flow.provider.calls) == 0


@pytest.mark.parametrize("problem", ["missing", "version", "prompt"])
def test_invalid_asset_admission_creates_no_task(flow, problem):
    asset = create_asset(flow)
    body = request(flow, asset)
    if problem == "missing":
        body["source"]["asset_id"] = "1"
    elif problem == "version":
        body["source"]["row_version"] = "999"
    else:
        body["input"]["prompt"] = ""
    with pytest.raises((NotFound, WorkflowError, GenerationRequestError)) as error:
        flow.generations.create("image", body, str(uuid4()))
    expected = {
        "missing": ("not_found", 404),
        "version": ("asset_version_conflict", 409),
        "prompt": ("generation_unsupported_parameters", 400),
    }
    assert (error.value.code, error.value.status_code) == expected[problem]
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_expired_worker_cannot_change_group_from_saved_output(flow):
    from short_drama.dao.task_runtime_dao import LeaseLost

    asset = create_asset(flow)
    task_id = submit(flow, asset)
    assert drain(flow, task_id) == "succeeded"
    before = groups(flow).current("asset", asset.id)
    with flow.factory() as session:
        task = session.get(AsyncTask, int(task_id))
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
        )
        entry = deepcopy(record.response_data["media_manifest"][0])
    with pytest.raises(LeaseLost):
        flow.executor.archive.save_one(task, record, entry, task.message_version, "expired-token")
    assert groups(flow).current("asset", asset.id) == before
