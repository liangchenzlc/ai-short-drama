from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.schemas.generations.batches.batch import BatchPreflight
from short_drama.service.generations.batches.batch import batch_status, item_status


def test_batch_unknown_never_reports_completion_or_releases_as_normal_failure():
    task = SimpleNamespace(status="failed", error={"code": "provider_acceptance_unknown"})
    assert item_status(task, SimpleNamespace(status="unknown")) == "needs_review"
    assert batch_status(["succeeded", "needs_review"], "running") == "needs_review"
    assert batch_status(["succeeded", "failed"], "running") == "partial"
    assert batch_status(["succeeded", "waiting"], "paused") == "paused"
    assert batch_status(["succeeded", "succeeded"], "running") == "succeeded"


def test_batch_scope_and_limits_are_server_validated():
    base = {
        "scene": "shot_image",
        "config_id": "1",
        "scope": {"library": "project", "project_id": "3"},
    }
    assert BatchPreflight.model_validate(base).count == 1
    for patch in [
        {"source_ids": ["4", "4"]},
        {"source_ids": list(map(str, range(1, 102)))},
        {"scope": {"library": "global"}},
        {"count": 5},
        {"scene": "shot_video", "count": 2},
    ]:
        with pytest.raises(ValidationError):
            BatchPreflight.model_validate({**base, **patch})


@pytest.fixture
def image_batch_flow():
    import base64

    from pydantic import SecretStr

    from short_drama.core.crypto import KeyCipher
    from short_drama.service.creation.assets.management.assets import AssetService
    from short_drama.service.generations.batches.batch import GenerationBatchService
    from short_drama.service.projects.management.project import ProjectService
    from tests.support.generation_fixtures import config, generation_session, settings

    session = generation_session()
    key = base64.b64encode(b"batch-test-key!!" * 2).decode()
    model = config(session)
    model.base_url = "https://relay.example"
    model.model_key = "gpt-image-2.5-flare"
    model.apikey = KeyCipher(key).encrypt("fixture-only")
    session.commit()
    project = ProjectService(session).create({"name": "Batch"})
    asset = AssetService(session).create(
        {"kind": "prop", "name": "Lantern", "description": "red lantern"},
        project_id=project.id,
    )
    batch_settings = SimpleNamespace(
        **vars(settings),
        encryption_key=SecretStr(key),
        generation_batches_enabled=True,
        generation_batch_image_concurrency=2,
    )
    service = GenerationBatchService(session, batch_settings)
    body = {
        "scene": "asset_image",
        "config_id": str(model.id),
        "scope": {"project_id": str(project.id)},
        "source_ids": [str(asset.id)],
    }
    try:
        yield SimpleNamespace(
            session=session, model=model, settings=batch_settings, service=service, body=body
        )
    finally:
        session.close()
        session.get_bind().dispose()


@pytest.mark.parametrize("mode", ["explicit", "explicit_old_cache", "verified_cache"])
def test_image_batch_preflight_matches_single_protocol_and_freezes_model_version(
    image_batch_flow, mode
):
    from copy import deepcopy

    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from short_drama.ai.adapters.catalog import capability_fingerprint
    from short_drama.ai.model_identity import model_credential_identity
    from short_drama.core.exceptions import WorkflowError
    from short_drama.domain import AIGenerationRecord, AsyncTask, GenerationBatchJob
    from short_drama.service.generations.admission.generation import AIGenerationService
    from short_drama.service.generations.batches.batch import dispatch_batches

    flow = image_batch_flow
    model = flow.model
    if mode.startswith("explicit"):
        model.runtime_profile = {"version": 1, "api_format": "openai", "protocol": "openai-image"}
    if mode != "explicit":
        snapshot = {key: getattr(model, key) for key in ("base_url", "model_key", "service_type")}
        model.capability_cache = {
            "adapter": "ark_images.v1" if mode == "explicit_old_cache" else "openai_images.v1",
            "fingerprint": capability_fingerprint(snapshot, model_credential_identity(model)),
        }
    flow.session.commit()
    before = model.row_version, deepcopy(model.capability_cache)
    preview = flow.service.preflight(flow.body)
    assert preview["items"][0]["state"] == "ready"
    assert preview["task_count"] == 1
    assert flow.service.preflight(flow.body)["preflight_hash"] == preview["preflight_hash"]
    assert (model.row_version, model.capability_cache) == before

    request = {
        **flow.body,
        "preflight_hash": preview["preflight_hash"],
        "accepted_ids": flow.body["source_ids"],
    }
    model.name = "Changed model"
    model.row_version += 1
    flow.session.commit()
    with pytest.raises(WorkflowError) as stale:
        flow.service.create(request, "stale-preview")
    assert stale.value.code == "batch_preflight_changed"
    fresh = flow.service.preflight(flow.body)
    assert fresh["preflight_hash"] != preview["preflight_hash"]
    request["preflight_hash"] = fresh["preflight_hash"]
    batch, created = flow.service.create(request, "relay-batch")
    replay, created_again = flow.service.create(request, "relay-batch")
    assert created and not created_again and replay["id"] == batch["id"]
    record = flow.session.scalar(select(AIGenerationRecord))
    task = flow.session.get(AsyncTask, record.task_id)
    stored = flow.session.get(GenerationBatchJob, int(batch["id"]))
    assert record.adapter == "openai_images.v1"
    assert stored.config_version == model.row_version == int(record.config_snapshot["row_version"])
    assert task.message_status == "idle"
    flow.session.commit()
    factory = sessionmaker(bind=flow.session.get_bind(), expire_on_commit=False)
    assert dispatch_batches(factory, flow.settings) == 1
    single, single_created = AIGenerationService(flow.session, flow.settings).create(
        "image", {"config_id": str(model.id), "input": {"prompt": "another image"}}, "relay-single"
    )
    single_record = flow.session.scalar(
        select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(single["generation_id"]))
    )
    assert single_created and single_record.adapter == record.adapter


def test_image_batch_rejects_capability_cache_for_another_credential(image_batch_flow):
    from short_drama.ai.adapters.catalog import capability_fingerprint

    flow = image_batch_flow
    model = flow.model
    snapshot = {key: getattr(model, key) for key in ("base_url", "model_key", "service_type")}
    model.capability_cache = {
        "adapter": "openai_images.v1",
        "fingerprint": capability_fingerprint(snapshot, "another-credential"),
    }
    flow.session.commit()
    preview = flow.service.preflight(flow.body)
    assert preview["task_count"] == 0
    assert preview["items"][0]["state"] == "blocked"


def test_image_batch_blocks_an_invalid_explicit_protocol_without_writing_config(image_batch_flow):
    flow = image_batch_flow
    flow.model.runtime_profile = {"version": 1, "api_format": "gemini", "protocol": "openai-image"}
    flow.session.commit()
    version = flow.model.row_version
    preview = flow.service.preflight(flow.body)
    assert preview["task_count"] == 0
    assert preview["items"][0]["state"] == "blocked"
    assert flow.model.row_version == version and flow.model.capability_cache is None


def test_image_batch_preflight_hash_changes_when_cached_adapter_changes(image_batch_flow):
    from short_drama.ai.adapters.catalog import capability_fingerprint
    from short_drama.ai.model_identity import model_credential_identity
    from short_drama.core.exceptions import WorkflowError

    flow = image_batch_flow
    model = flow.model
    snapshot = {key: getattr(model, key) for key in ("base_url", "model_key", "service_type")}
    fingerprint = capability_fingerprint(snapshot, model_credential_identity(model))
    model.capability_cache = {"adapter": "openai_images.v1", "fingerprint": fingerprint}
    flow.session.commit()
    preview = flow.service.preflight(flow.body)
    assert preview["task_count"] == 1
    model.capability_cache = {"adapter": "ark_images.v1", "fingerprint": fingerprint}
    flow.session.commit()
    changed = flow.service.preflight(flow.body)
    assert changed["task_count"] == 1
    assert changed["preflight_hash"] != preview["preflight_hash"]
    request = {
        **flow.body,
        "preflight_hash": preview["preflight_hash"],
        "accepted_ids": flow.body["source_ids"],
    }
    with pytest.raises(WorkflowError) as stale:
        flow.service.create(request, "cached-adapter-changed")
    assert stale.value.code == "batch_preflight_changed"


def test_batch_setting_is_enabled_by_default_and_explicit_false_disables(monkeypatch):
    from short_drama.api.v1.generations.batches.batches import capabilities
    from short_drama.core.config import Settings
    from short_drama.service.generations.batches.batch import dispatch_batches

    monkeypatch.setenv("GENERATION_BATCHES_ENABLED", "false")
    disabled = Settings(_env_file=None)
    assert not disabled.generation_batches_enabled
    assert not capabilities(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=disabled)))
    )["enabled"]
    assert (
        dispatch_batches(lambda: pytest.fail("disabled scheduler touched database"), disabled) == 0
    )
    monkeypatch.delenv("GENERATION_BATCHES_ENABLED")
    enabled = Settings(_env_file=None)
    assert enabled.generation_batches_enabled
    assert capabilities(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=enabled)))
    )["enabled"]


def test_video_batch_uses_each_shots_saved_prompt_and_references():
    from sqlalchemy import select

    from short_drama.domain import AIGenerationRecord
    from short_drama.service.creation.drafts.drafts import UserCreationDraftService
    from short_drama.service.generations.batches.batch import GenerationBatchService
    from tests.support.generation_fixtures import generation_session, settings
    from tests.unit.standard.shot_video.test_shot_video_generation import setup_video

    with generation_session() as session:
        flow = setup_video(session)
        UserCreationDraftService(session).create_for_scope(
            flow.project,
            {
                "object_id": flow.shot_id,
                "purpose": "storyboard_video",
                "model_config_id": "1",
                "input_text": "镜头缓慢推进",
                "video_references": [{"type": "shot_image", "media_id": "101"}],
            },
        )
        batch_settings = SimpleNamespace(
            **vars(settings), generation_batches_enabled=True, generation_batch_video_concurrency=2
        )
        service = GenerationBatchService(session, batch_settings)
        body = {
            "scene": "shot_video",
            "config_id": "1",
            "scope": {"project_id": str(flow.project)},
            "source_ids": [flow.shot_id],
        }
        preview = service.preflight(body)
        assert preview["task_count"] == 1
        service.create(
            {**body, "preflight_hash": preview["preflight_hash"], "accepted_ids": [flow.shot_id]},
            "video-batch-draft",
        )
        record = session.scalar(select(AIGenerationRecord))
        assert record.request_data["input"]["reference_media_ids"] == ["101"]
        assert "镜头缓慢推进" in record.request_data["input"]["prompt"]


def test_video_batch_defaults_to_linked_assets_when_no_draft_exists():
    from sqlalchemy import select

    from short_drama.domain import AIGenerationRecord, MediaFile
    from short_drama.service.creation.assets.management.assets import AssetService
    from short_drama.service.generations.batches.batch import GenerationBatchService
    from tests.support.generation_fixtures import generation_session, settings
    from tests.unit.standard.shot_video.test_shot_video_generation import current, setup_video

    with generation_session() as session:
        flow = setup_video(session)
        session.add(
            MediaFile(
                id=501,
                project_id=flow.project,
                format_code="image/png",
                storage_locator="minio://images/batch-character.png",
                original_name="",
            )
        )
        session.commit()
        asset = AssetService(session).create(
            {"kind": "character", "name": "Lin", "media_id": "501"},
            project_id=flow.project,
        )
        shot = current(flow)
        flow.storyboard.update(
            flow.project,
            flow.shot_id,
            {"row_version": shot["row_version"], "asset_ids": [str(asset.id)]},
        )
        batch_settings = SimpleNamespace(
            **vars(settings), generation_batches_enabled=True, generation_batch_video_concurrency=2
        )
        service = GenerationBatchService(session, batch_settings)
        body = {
            "scene": "shot_video",
            "config_id": "1",
            "scope": {"project_id": str(flow.project)},
            "source_ids": [flow.shot_id],
        }
        preview = service.preflight(body)
        assert preview["task_count"] == 1
        service.create(
            {**body, "preflight_hash": preview["preflight_hash"], "accepted_ids": [flow.shot_id]},
            "video-batch-default-assets",
        )
        record = session.scalar(select(AIGenerationRecord))
        assert record.request_data["input"]["reference_media_ids"] == ["501"]
