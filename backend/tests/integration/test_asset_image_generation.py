"""Asset image business flow on disposable MySQL; providers/storage are local doubles."""

import base64
from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import delete, func, select

from short_drama.ai import GenerationResult
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.db.session import session_factory
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AssetImageCandidate,
    AsyncTask,
    MediaAsset,
    MediaFile,
    MediaRecycleBin,
)
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.asset_image_service import AssetImageService
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.asset_service import AssetService
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.service.media_asset_service import MediaAssetService
from short_drama.service.project_service import ProjectService
from short_drama.service.shot_asset_service import ShotAssetService
from short_drama.service.shot_script_service import ShotScriptService
from short_drama.storage.models import ObjectLocation, StoredObject

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
            "model_key": "fixture",
            "provider": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        }
    )
    project = ProjectService(db_session).create({"name": "asset flow", "aspect": "16:9"})
    episode = EpisodeService(db_session).create(
        {
            "project_id": project.id,
            "position": 1,
            "title": "test",
            "aspect": "16:9",
        }
    )
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
        episode=episode,
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
    pytest.fail("Controlled image task did not finish")


def submit(flow, asset, count=1):
    task, created = flow.generations.create("image", request(flow, asset, count), str(uuid4()))
    assert created
    return task["generation_id"]


def submit_shot(flow, shot_id, count=1):
    shot = EpisodeStoryboardService(flow.session).get(flow.project.id, flow.episode.id, shot_id)[
        "shot"
    ]
    task, created = flow.generations.create(
        "image",
        {
            "config_id": str(flow.config.id),
            "input": {"prompt": ""},
            "parameters": {"count": count, "aspect": "16:9", "resolution": "2K"},
            "source": {
                "scene": "shot_image",
                "shot_id": str(shot_id),
                "layout": "single",
                "context_mode": "saved",
                "row_version": shot["row_version"],
                "context_hash": shot["context_hash"],
            },
        },
        str(uuid4()),
    )
    assert created
    return task["generation_id"]


@pytest.mark.parametrize("kind", ["character", "scene", "prop"])
def test_generated_candidates_require_adoption_then_supply_shot_references(flow, kind):
    asset = create_asset(flow, kind)
    task_id = submit(flow, asset, 2)
    assert drain(flow, task_id) == "succeeded"
    assert len(flow.provider.calls) == 1
    candidates = flow.images.list(asset.id)
    assert candidates["total"] == 2
    unchanged = flow.library.get(asset.id)
    assert (unchanged.media_id, unchanged.state, unchanged.row_version) == (
        None,
        "unconfirmed",
        asset.row_version,
    )
    first, second = candidates["items"]
    assert first.generation.generation_id == int(task_id)
    assert not first.generation.is_stale
    confirmed = flow.images.confirm(
        asset.id,
        {
            "row_version": str(asset.row_version),
            "expected_media_id": None,
            "media_id": str(first.media_id),
        },
    )
    assert confirmed.state == "confirmed"
    assert all(not item.generation.is_stale for item in flow.images.list(asset.id)["items"])
    repeated = flow.images.confirm(
        asset.id,
        {
            "row_version": str(confirmed.row_version),
            "expected_media_id": str(first.media_id),
            "media_id": str(first.media_id),
        },
    )
    assert repeated.row_version == confirmed.row_version
    replaced = flow.images.confirm(
        asset.id,
        {
            "row_version": str(confirmed.row_version),
            "expected_media_id": str(first.media_id),
            "media_id": str(second.media_id),
        },
    )
    flow.library.link("episode", flow.episode.id, flow.project.id, asset.id)
    shot = ShotScriptService(flow.session).create(
        {"episode_id": flow.episode.id, "position": 1, "script": "A figure enters."}
    )
    ShotAssetService(flow.session).create(
        {"episode_id": flow.episode.id, "shot_id": shot.id, "asset_id": asset.id}
    )
    board = EpisodeStoryboardService(flow.session)
    context = board.get_shot_context(flow.project.id, flow.episode.id, shot.id)
    with flow.factory() as session:
        from short_drama.domain import ShotScript

        version = session.get(ShotScript, shot.id).row_version
    shot_task, _ = flow.generations.create(
        "image",
        {
            "config_id": str(flow.config.id),
            "input": {"prompt": ""},
            "parameters": {"count": 1, "aspect": "16:9", "resolution": "2K"},
            "source": {
                "scene": "shot_image",
                "shot_id": str(shot.id),
                "layout": "single",
                "context_mode": "saved",
                "row_version": str(version),
                "context_hash": context["context_hash"],
            },
        },
        str(uuid4()),
    )
    snapshot = flow.generations.detail(shot_task["generation_id"])
    assert str(replaced.media_id) in snapshot["input"]["reference_media_ids"]
    assert str(first.media_id) not in snapshot["input"]["reference_media_ids"]
    assert drain(flow, shot_task["generation_id"]) == "succeeded"
    detail = flow.generations.detail(shot_task["generation_id"])
    assert len(detail["result"]["assets"]) == 1
    candidate = detail["result"]["assets"][0]
    before = board.get(flow.project.id, flow.episode.id, shot.id)["shot"]
    assert before["image"] is None
    applied = flow.media.apply(
        candidate["asset_id"],
        {
            "target": {"type": "shot_image", "id": str(shot.id)},
            "expected_media_id": None,
            "expected_row_version": before["row_version"],
            "expected_context_hash": before["context_hash"],
        },
    )
    with flow.factory() as session:
        after = EpisodeStoryboardService(session).get(flow.project.id, flow.episode.id, shot.id)[
            "shot"
        ]
        assert after["image"]["media_id"] == candidate["media_id"]
        assert after["image"]["is_stale"] is False
        assert after["row_version"] == applied["row_version"]
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 3
    assert len(flow.provider.calls) == 2
    assert flow.provider.calls[-1]["input"]["reference_media_ids"] == [str(second.media_id)]
    assert flow.provider.calls[-1]["input"]["reference_urls"] == [second.url]


def test_shot_references_deduplicate_confirmed_assets_and_exclude_unconfirmed_images(flow):
    assets = []
    confirmed_media = []
    reference_urls = []
    unused_candidates = []
    for kind in ("character", "scene", "prop", "character"):
        asset = create_asset(flow, kind)
        task_id = submit(flow, asset, 2)
        assert drain(flow, task_id) == "succeeded"
        first, second = flow.images.list(asset.id)["items"]
        confirmed = flow.images.confirm(
            asset.id,
            {
                "row_version": str(asset.row_version),
                "expected_media_id": None,
                "media_id": str(first.media_id),
            },
        )
        assets.append(confirmed)
        confirmed_media.append(str(first.media_id))
        reference_urls.append(first.url)
        unused_candidates.append(str(second.media_id))
    unconfirmed = flow.library.patch(
        assets[-1].id,
        {"row_version": str(assets[-1].row_version), "description": "changed costume"},
    )
    assert unconfirmed.state == "unconfirmed" and unconfirmed.media_id is not None
    duplicate = create_asset(flow, "prop")
    flow.images.add_candidate(duplicate.id, assets[0].media_id)
    duplicate = flow.images.confirm(
        duplicate.id,
        {
            "row_version": str(duplicate.row_version),
            "expected_media_id": None,
            "media_id": str(assets[0].media_id),
            "acknowledge_stale_source": True,
        },
    )
    pending = create_asset(flow, "scene")
    pending_task = submit(flow, pending)
    assert drain(flow, pending_task) == "succeeded"
    unused_candidates.append(str(flow.images.list(pending.id)["items"][0].media_id))
    assets.extend([duplicate, pending])
    shot = ShotScriptService(flow.session).create(
        {"episode_id": flow.episode.id, "position": 1, "script": "All enter the garden."}
    )
    for asset in reversed(assets):
        flow.library.link("episode", flow.episode.id, flow.project.id, asset.id)
        ShotAssetService(flow.session).create(
            {"episode_id": flow.episode.id, "shot_id": shot.id, "asset_id": asset.id}
        )
    task_id = submit_shot(flow, shot.id)
    references = flow.generations.detail(task_id)["input"]["reference_media_ids"]
    assert references == confirmed_media[:3]
    assert confirmed_media[-1] not in references
    assert not set(unused_candidates).intersection(references)
    assert drain(flow, task_id) == "succeeded"
    assert flow.provider.calls[-1]["input"]["reference_media_ids"] == confirmed_media[:3]
    assert flow.provider.calls[-1]["input"]["reference_urls"] == reference_urls[:3]
    assert len(flow.provider.calls) == 6


def test_historical_shot_candidate_preserves_parameters_and_requires_current_target(flow):
    asset = create_asset(flow, "character")
    task_id = submit(flow, asset, 2)
    assert drain(flow, task_id) == "succeeded"
    first, second = flow.images.list(asset.id)["items"]
    confirmed = flow.images.confirm(
        asset.id,
        {
            "row_version": str(asset.row_version),
            "expected_media_id": None,
            "media_id": str(first.media_id),
        },
    )
    flow.library.link("episode", flow.episode.id, flow.project.id, asset.id)
    shot = ShotScriptService(flow.session).create(
        {"episode_id": flow.episode.id, "position": 1, "script": "A figure enters."}
    )
    ShotAssetService(flow.session).create(
        {"episode_id": flow.episode.id, "shot_id": shot.id, "asset_id": asset.id}
    )
    board = EpisodeStoryboardService(flow.session)
    original = board.get(flow.project.id, flow.episode.id, shot.id)["shot"]
    shot_task_id = submit_shot(flow, shot.id, 2)
    assert drain(flow, shot_task_id) == "succeeded"
    candidates = flow.generations.detail(shot_task_id)["result"]["assets"]
    flow.images.confirm(
        asset.id,
        {
            "row_version": str(confirmed.row_version),
            "expected_media_id": str(first.media_id),
            "media_id": str(second.media_id),
            "confirm_shared": True,
        },
    )
    changed_source = board.get(flow.project.id, flow.episode.id, shot.id)["shot"]
    assert changed_source["row_version"] == original["row_version"]
    assert changed_source["context_hash"] != original["context_hash"]
    body = {
        "target": {"type": "shot_image", "id": str(shot.id)},
        "expected_media_id": None,
        "expected_row_version": changed_source["row_version"],
        "expected_context_hash": changed_source["context_hash"],
    }
    with pytest.raises(WorkflowError) as stale:
        flow.media.apply(candidates[0]["asset_id"], body)
    assert stale.value.code == "stale_generation_source"
    current = board.update(
        flow.project.id,
        flow.episode.id,
        shot.id,
        {
            "row_version": changed_source["row_version"],
            "image_settings": {"layout": "single", "aspect": "inherit", "resolution": "4K"},
        },
    )["shot"]
    body.update(
        expected_row_version=current["row_version"],
        expected_context_hash=current["context_hash"],
        acknowledge_stale_source=True,
    )
    for stale_token in (
        {"expected_row_version": original["row_version"]},
        {"expected_context_hash": original["context_hash"]},
        {"expected_media_id": str(first.media_id)},
    ):
        with pytest.raises(WorkflowError) as conflict:
            flow.media.apply(candidates[0]["asset_id"], {**body, **stale_token})
        assert conflict.value.code == "shot_version_conflict"
    with pytest.raises(WorkflowError) as wrong_parameters:
        flow.media.apply(candidates[0]["asset_id"], {**body, "parameters": {"resolution": "4K"}})
    assert wrong_parameters.value.code == "generation_parameters_conflict"
    assert board.get(flow.project.id, flow.episode.id, shot.id)["shot"]["image"] is None
    applied = flow.media.apply(candidates[0]["asset_id"], body)
    assert flow.media.apply(candidates[0]["asset_id"], body) == applied
    with flow.factory() as session:
        saved = EpisodeStoryboardService(session).get(flow.project.id, flow.episode.id, shot.id)[
            "shot"
        ]
        assert saved["image"]["media_id"] == candidates[0]["media_id"]
        assert saved["image"]["resolution"] == "2K"
        assert saved["image"]["aspect"] == "16:9"
        assert saved["image"]["layout"] == "single"
        assert saved["image"]["is_stale"] is False
        assert saved["image_settings"]["resolution"] == "4K"
        assert session.scalar(select(func.count()).select_from(MediaRecycleBin)) == 0
    replacement_body = {
        **body,
        "expected_row_version": applied["row_version"],
        "expected_context_hash": applied["context_hash"],
        "expected_media_id": candidates[0]["media_id"],
    }
    replaced = flow.media.apply(candidates[1]["asset_id"], replacement_body)
    assert flow.media.apply(candidates[1]["asset_id"], replacement_body) == replaced
    with flow.factory() as session:
        saved = EpisodeStoryboardService(session).get(flow.project.id, flow.episode.id, shot.id)[
            "shot"
        ]
        assert saved["image"]["media_id"] == candidates[1]["media_id"]
        assert saved["image"]["resolution"] == "2K"
        assert saved["image_settings"]["resolution"] == "4K"
        assert session.scalar(select(func.count()).select_from(MediaRecycleBin)) == 1
        old_media = session.get(MediaFile, int(candidates[0]["media_id"]))
        assert old_media is not None
        location = ObjectLocation.parse(
            old_media.storage_locator, {flow.settings.minio_image_bucket}
        )
        assert flow.storage.stat(location.bucket, location.object_name).size > 0
    other_shot = ShotScriptService(flow.session).create(
        {"episode_id": flow.episode.id, "position": 2, "script": "Another view."}
    )
    other = board.get(flow.project.id, flow.episode.id, other_shot.id)["shot"]
    other_body = {
        "target": {"type": "shot_image", "id": str(other_shot.id)},
        "expected_media_id": None,
        "expected_row_version": other["row_version"],
        "expected_context_hash": other["context_hash"],
    }
    with pytest.raises(WorkflowError) as other_source:
        flow.media.apply(candidates[0]["asset_id"], other_body)
    assert other_source.value.code == "stale_generation_source"
    assert board.get(flow.project.id, flow.episode.id, other_shot.id)["shot"]["image"] is None
    adopted_elsewhere = flow.media.apply(
        candidates[0]["asset_id"], {**other_body, "acknowledge_stale_source": True}
    )
    assert adopted_elsewhere["media_id"] == candidates[0]["media_id"]
    assert len(flow.provider.calls) == 2


@pytest.mark.parametrize("via_media", [False, True])
def test_stale_and_shared_adoption_checks_cannot_bypass_version(flow, via_media):
    asset = create_asset(flow)
    task_id = submit(flow, asset)
    assert drain(flow, task_id) == "succeeded"
    candidate = flow.images.list(asset.id)["items"][0]
    changed = flow.library.patch(
        asset.id, {"row_version": str(asset.row_version), "description": "red silk"}
    )
    flow.library.link("episode", flow.episode.id, flow.project.id, asset.id)
    assert flow.images.list(asset.id)["items"][0].generation.is_stale
    with flow.factory() as session:
        media_asset_id = session.scalar(
            select(MediaAsset.id).where(MediaAsset.media_id == candidate.media_id)
        )

    def adopt(version, stale=False, shared=False):
        if via_media:
            return flow.media.apply(
                media_asset_id,
                {
                    "target": {"type": "asset_image", "id": str(asset.id)},
                    "expected_row_version": str(version),
                    "expected_media_id": None,
                    "acknowledge_stale_source": stale,
                    "confirm_shared": shared,
                },
            )
        return flow.images.confirm(
            asset.id,
            {
                "row_version": str(version),
                "expected_media_id": None,
                "media_id": str(candidate.media_id),
                "acknowledge_stale_source": stale,
                "confirm_shared": shared,
            },
        )

    for version, stale, shared, code in [
        (asset.row_version, True, True, "asset_version_conflict"),
        (changed.row_version, False, False, "stale_source"),
        (changed.row_version, True, False, "shared_asset_confirmation_required"),
    ]:
        with pytest.raises(WorkflowError) as error:
            adopt(version, stale, shared)
        assert error.value.code == code
    adopt(changed.row_version, True, True)
    assert flow.library.get(asset.id).media_id == candidate.media_id


def test_idempotent_replay_after_edit_and_history_scope(flow):
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
    assert "blue linen" in flow.provider.calls[0]["input"]["prompt"]
    assert "new green material" not in flow.provider.calls[0]["input"]["prompt"]
    linked = flow.library.link("episode", flow.episode.id, flow.project.id, asset.id)
    assert linked.id == asset.id
    filters = {"source_scene": "asset_image", "source_id": str(linked.id)}
    assert flow.generations.list(filters=filters)["total"] == 1
    assert flow.media.list(filters=filters)["total"] == 1
    copied = AssetService(flow.session).copy_for_library(
        "episode", linked.link_id, {"name": "copy"}
    )
    assert copied.id != asset.id
    assert flow.generations.list(filters={**filters, "source_id": str(copied.id)})["total"] == 0
    other = create_asset(flow, "character")
    assert flow.generations.list(filters={**filters, "source_id": str(other.id)})["total"] == 0


def test_candidate_failure_rolls_back_archive_and_recovers_without_provider_call(flow, monkeypatch):
    asset = create_asset(flow)
    task_id = submit(flow, asset)
    flow.executor.execute(task_id, 1)
    original = flow.executor.archive._link_asset_candidate

    def unavailable(*_args):
        raise RuntimeError("controlled database failure")

    monkeypatch.setattr(flow.executor.archive, "_link_asset_candidate", unavailable)
    with flow.factory() as session:
        version = session.get(AsyncTask, int(task_id)).message_version
    flow.executor.execute(task_id, version)
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(MediaAsset)) == 0
        assert session.scalar(select(func.count()).select_from(AssetImageCandidate)) == 0
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
        )
        assert not record.response_data["media_manifest"][0].get("saved")
    monkeypatch.setattr(flow.executor.archive, "_link_asset_candidate", original)
    with flow.factory.begin() as session:
        session.get(AsyncTask, int(task_id)).next_run_at = utcnow()
    assert drain(flow, task_id) == "succeeded"
    assert len(flow.provider.calls) == 1
    assert flow.images.list(asset.id)["total"] == 1


@pytest.mark.parametrize("saved_flag", [False, True])
def test_existing_output_repairs_candidate_and_manifest_without_redownload(
    flow, saved_flag, monkeypatch
):
    asset = create_asset(flow)
    task_id = submit(flow, asset)
    assert drain(flow, task_id) == "succeeded"
    with flow.factory.begin() as session:
        session.execute(delete(AssetImageCandidate).where(AssetImageCandidate.asset_id == asset.id))
        task = session.get(AsyncTask, int(task_id))
        task.status, task.error = "failed", {"code": "archive_failed"}
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
        )
        response = deepcopy(record.response_data)
        response["media_manifest"][0].pop("candidate_status", None)
        response["media_manifest"][0]["saved"] = saved_flag
        record.response_data = response

    def storage_unavailable(*_args):
        raise AssertionError("Existing output recovery must stay local to the database")

    monkeypatch.setattr(flow.storage, "stat", storage_unavailable)
    flow.generations.resume(task_id)
    assert drain(flow, task_id) == "succeeded"
    assert flow.images.list(asset.id)["total"] == 1
    assert len(flow.provider.calls) == 1


def test_missing_source_keeps_paid_output_and_exposes_warning(flow):
    from short_drama.domain import ProjectAsset

    asset = create_asset(flow)
    task_id = submit(flow, asset)
    # JSON business sources deliberately have no FK; deletion can race a paid call.
    with flow.factory.begin() as session:
        session.execute(delete(ProjectAsset).where(ProjectAsset.asset_id == asset.id))
        session.execute(delete(Asset).where(Asset.id == asset.id))
    assert drain(flow, task_id) == "succeeded"
    detail = flow.generations.detail(task_id)
    assert len(detail["result"]["assets"]) == 1
    assert detail["result"]["warnings"][0] == {
        "code": "asset_source_missing",
        "message": "原素材已不存在，图片已保存至媒体库",
        "output_index": 1,
    }
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(AssetImageCandidate)) == 0
    assert len(flow.provider.calls) == 1


def test_partial_result_preserves_candidates_and_never_adopts(flow, monkeypatch):
    asset = create_asset(flow)
    original = flow.provider.submit

    def fewer_outputs(*args, **kwargs):
        result = original(*args, **kwargs)
        return GenerationResult(
            status=result.status, adapter=result.adapter, outputs=result.outputs[:2]
        )

    monkeypatch.setattr(flow.provider, "submit", fewer_outputs)
    task_id = submit(flow, asset, 3)
    assert drain(flow, task_id) == "failed"
    detail = flow.generations.detail(task_id)
    assert detail["error"]["code"] == "partial_result"
    assert detail["result"]["partial"] is True
    assert flow.images.list(asset.id)["total"] == 2
    assert flow.library.get(asset.id).media_id is None
    assert len(flow.provider.calls) == 1


def test_concurrent_adoption_allows_one_version_writer(flow):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    asset = create_asset(flow)
    task_id = submit(flow, asset, 2)
    assert drain(flow, task_id) == "succeeded"
    candidates = flow.images.list(asset.id)["items"]
    barrier = Barrier(2)

    def adopt(candidate):
        with Session(flow.session.get_bind(), expire_on_commit=False, autoflush=False) as session:
            barrier.wait(timeout=10)
            try:
                result = AssetImageService(session, flow.settings, flow.storage).confirm(
                    asset.id,
                    {
                        "row_version": str(asset.row_version),
                        "expected_media_id": None,
                        "media_id": str(candidate.media_id),
                    },
                )
                return "adopted", result.media_id
            except WorkflowError as error:
                return error.code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(adopt, candidates))
    assert sorted(outcome[0] for outcome in outcomes) == ["adopted", "asset_version_conflict"]
    flow.session.expire_all()
    actual = flow.library.get(asset.id)
    assert actual.row_version == asset.row_version + 1
    assert actual.media_id == next(media_id for state, media_id in outcomes if state == "adopted")


def test_existing_saved_output_repair_failure_remains_recoverable(flow, monkeypatch):
    asset = create_asset(flow)
    task_id = submit(flow, asset)
    assert drain(flow, task_id) == "succeeded"
    with flow.factory.begin() as session:
        session.execute(delete(AssetImageCandidate).where(AssetImageCandidate.asset_id == asset.id))
        task = session.get(AsyncTask, int(task_id))
        task.status, task.error = "failed", {"code": "archive_failed"}
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
        )
        response = deepcopy(record.response_data)
        response["media_manifest"][0].pop("candidate_status", None)
        record.response_data = response
    original = flow.executor.archive._link_asset_candidate

    def unavailable(*_args):
        raise RuntimeError("controlled failure while repairing already saved output")

    monkeypatch.setattr(flow.executor.archive, "_link_asset_candidate", unavailable)
    flow.generations.resume(task_id)
    with flow.factory() as session:
        version = session.get(AsyncTask, int(task_id)).message_version
    flow.executor.execute(task_id, version)
    with flow.factory() as session:
        task = session.get(AsyncTask, int(task_id))
        assert task.status == "running"
        assert task.next_action == "save"
        assert session.scalar(select(func.count()).select_from(AssetImageCandidate)) == 0
    monkeypatch.setattr(flow.executor.archive, "_link_asset_candidate", original)
    with flow.factory.begin() as session:
        session.get(AsyncTask, int(task_id)).next_run_at = utcnow()
    assert drain(flow, task_id) == "succeeded"
    assert flow.images.list(asset.id)["total"] == 1
    assert len(flow.provider.calls) == 1


@pytest.mark.parametrize("problem", ["missing", "version", "empty"])
def test_invalid_asset_admission_creates_no_task(flow, problem):
    asset = create_asset(flow)
    body = request(flow, asset)
    if problem == "missing":
        body["source"]["asset_id"] = "1"
    elif problem == "version":
        body["source"]["row_version"] = "999"
    else:
        asset = flow.library.patch(
            asset.id,
            {
                "row_version": str(asset.row_version),
                "description": "",
                "prompt": "",
            },
        )
        body = request(flow, asset)
    with pytest.raises((NotFound, WorkflowError)) as error:
        flow.generations.create("image", body, str(uuid4()))
    expected = {
        "missing": ("not_found", 404),
        "version": ("asset_version_conflict", 409),
        "empty": ("asset_content_required", 400),
    }
    assert (error.value.code, error.value.status_code) == expected[problem]
    with flow.factory() as session:
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


@pytest.mark.parametrize("reason", ["source_mismatch", "snapshot_missing"])
def test_candidate_provenance_requires_explicit_adoption_for_mismatched_or_missing_snapshot(
    flow, reason
):
    asset = create_asset(flow)
    task_id = submit(flow, asset)
    assert drain(flow, task_id) == "succeeded"
    candidate = flow.images.list(asset.id)["items"][0]
    if reason == "source_mismatch":
        asset = create_asset(flow, "character")
        candidate, _ = flow.images.add_candidate(asset.id, candidate.media_id)
    else:
        with flow.factory.begin() as session:
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
            )
            values = deepcopy(record.request_data)
            values["source_snapshot"].pop("asset_content_hash")
            record.request_data = values
        flow.session.expire_all()
        candidate = flow.images.list(asset.id)["items"][0]
    assert candidate.generation.stale_reason == reason
    body = {
        "row_version": str(asset.row_version),
        "expected_media_id": None,
        "media_id": str(candidate.media_id),
    }
    with pytest.raises(WorkflowError) as error:
        flow.images.confirm(asset.id, body)
    assert error.value.code == "stale_source"
    # Explicitly accepting old provenance must still protect the current media pointer.
    with pytest.raises(WorkflowError) as error:
        flow.images.confirm(
            asset.id, {**body, "expected_media_id": "999", "acknowledge_stale_source": True}
        )
    assert error.value.code == "asset_version_conflict"
    assert (
        flow.images.confirm(asset.id, {**body, "acknowledge_stale_source": True}).media_id
        == candidate.media_id
    )


def test_paid_retry_keeps_original_asset_snapshot_after_edit(flow):
    asset = create_asset(flow)
    task_id = submit(flow, asset)
    assert drain(flow, task_id) == "succeeded"
    original_input = flow.generations.detail(task_id)["input"]
    flow.library.patch(
        asset.id, {"row_version": str(asset.row_version), "prompt": "changed latest input"}
    )
    with flow.factory.begin() as session:
        task = session.get(AsyncTask, int(task_id))
        task.status, task.error = "failed", {"code": "partial_result"}
    retry, created = flow.generations.retry(task_id, {}, str(uuid4()))
    assert created
    assert flow.generations.detail(retry["generation_id"])["input"] == original_input
    assert drain(flow, retry["generation_id"]) == "succeeded"
    assert flow.provider.calls[-1]["input"] == original_input


def test_expired_worker_cannot_recreate_candidate_from_saved_output(flow):
    from short_drama.dao.task_runtime_dao import LeaseLost

    asset = create_asset(flow)
    task_id = submit(flow, asset)
    assert drain(flow, task_id) == "succeeded"
    with flow.factory.begin() as session:
        session.execute(delete(AssetImageCandidate).where(AssetImageCandidate.asset_id == asset.id))
        task = session.get(AsyncTask, int(task_id))
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
        )
        entry = deepcopy(record.response_data["media_manifest"][0])
    with pytest.raises(LeaseLost):
        flow.executor.archive.save_one(task, record, entry, task.message_version, "expired-token")
    assert flow.images.list(asset.id)["total"] == 0
