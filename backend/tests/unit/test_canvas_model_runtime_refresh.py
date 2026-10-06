"""迁入宿主真值的运行缓存刷新；不读取真实凭据或调用供应商。"""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from short_drama.ai.adapters import capability_fingerprint, select_adapter
from short_drama.ai.model_identity import model_credential_identity
from short_drama.ai.types import GenerationResult
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import AIGenerationRecord, AIModelConfig
from short_drama.service.base import utcnow
from short_drama.service.canvas_model_catalog_service import (
    CanvasModelCatalogService,
    protocol_adapter,
)
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.service.model_runtime_config import legacy_runtime_profile


def runtime_catalog(*, managed=False, protocol="openai-videos", mode="video", cache=None):
    model = "seedance-2.5" if managed else "fixture-model"
    profile = {
        "model": model,
        "capability": mode,
        "protocol": protocol,
        "capabilityConfig": {mode: {"streaming": False, "references": {"maxImages": 1}}},
        "defaultOptions": {"variants": [{"id": "source-variant"}]},
    }
    channel = {
        "id": "beefapi" if managed else "fixture-channel",
        "baseUrl": "https://enterprise.beefapi.com/v1"
        if managed
        else "https://provider.example/v1",
        "enabled": True,
        "models": [model],
        "modelProfiles": [profile],
    }
    config = SimpleNamespace(
        id=17,
        owner_user_id=3,
        name="Saved name",
        provider=protocol,
        model_key=model,
        service_type=mode,
        base_url=channel["baseUrl"],
        apikey="synthetic-encrypted-envelope",
        enabled=1,
        is_deleted=0,
        is_default=0,
        row_version=7,
        updated_at=utcnow(),
        updated_by=3,
        capability_cache=deepcopy(cache),
        runtime_profile=legacy_runtime_profile(channel, profile),
    )
    binding = SimpleNamespace(
        user_id=3, channel_key=channel["id"], model_key=model, model_config_id=config.id
    )
    stored = SimpleNamespace(
        user_id=3, channels_json=[channel], credentials_cipher="unchanged-catalog-envelope"
    )
    service = object.__new__(CanvasModelCatalogService)
    service.session = SimpleNamespace(info={"actor": SimpleNamespace(user_id=3)}, new=[])
    service.catalog_dao = SimpleNamespace(
        binding=lambda identifier: binding,
        catalog=lambda user_id, **kwargs: stored,
    )
    return service, config, channel, profile, binding, stored


def snapshot(config):
    return {
        "base_url": config.base_url,
        "model_key": config.model_key,
        "service_type": config.service_type,
        "credential_identity": model_credential_identity(config),
        "capability_cache": deepcopy(config.capability_cache),
        "row_version": str(config.row_version),
    }


@pytest.mark.parametrize(
    ("managed", "protocol"),
    [(False, "openai-videos"), (False, "newapi-channel-2"), (True, "volcengine-ark-video")],
)
@pytest.mark.parametrize("old_cache", ["none", "valid_old_ark", "expired_fingerprint"])
def test_migrated_host_profile_refreshes_exact_current_protocol_without_resave(
    managed, protocol, old_cache
):
    service, config, channel, profile, _, stored = runtime_catalog(
        managed=managed, protocol=protocol
    )
    if old_cache != "none":
        state = snapshot(config)
        config.capability_cache = {
            "adapter": "ark_video.v1",
            "fingerprint": capability_fingerprint(state, state["credential_identity"])
            if old_cache == "valid_old_ark"
            else "expired",
        }
    frozen_old_call = snapshot(config)
    original = deepcopy(vars(config))
    saved_channels = deepcopy(stored.channels_json)
    service.refresh_runtime_model_locked(config)
    assert select_adapter(snapshot(config)) == protocol_adapter(channel, profile)
    assert (
        config.capability_cache["canvas_video_capability"] == profile["capabilityConfig"]["video"]
    )
    assert config.capability_cache["canvas_channel_key"] == channel["id"]
    assert config.row_version == 8
    assert stored.channels_json == saved_channels
    assert stored.credentials_cipher == "unchanged-catalog-envelope"
    for field in ("apikey", "enabled", "is_deleted", "is_default", "base_url", "name", "provider"):
        assert getattr(config, field) == original[field]
    assert frozen_old_call["capability_cache"] == original["capability_cache"]
    refreshed = deepcopy(vars(config))
    service.refresh_runtime_model_locked(config)
    assert vars(config) == refreshed, "The same derived cache must be a complete no-op"


def test_refresh_deepcopies_saved_text_capability_and_never_guesses_model_vision():
    service, config, _, profile, _, _ = runtime_catalog(protocol="chat-completion", mode="text")
    service.refresh_runtime_model_locked(config)
    expected = deepcopy(profile["capabilityConfig"]["text"])
    assert config.capability_cache["canvas_text_capability"] == expected
    profile["capabilityConfig"]["text"]["references"]["maxImages"] = 12
    assert config.capability_cache["canvas_text_capability"] == expected
    config.runtime_profile["capability_config"] = {}
    config.model_key = profile["model"] = "gpt-6-image-capable-name"
    service.catalog_dao.binding(17).model_key = config.model_key
    service.catalog_dao.catalog(3).channels_json[0]["models"] = [config.model_key]
    service.refresh_runtime_model_locked(config)
    assert "canvas_text_capability" not in config.capability_cache


def test_unbound_standard_model_is_not_changed_or_activated():
    service, config, _, _, _, _ = runtime_catalog(cache={"adapter": "old-standard"})
    config.runtime_profile = None
    service.catalog_dao.binding = lambda identifier: None
    original = deepcopy(vars(config))
    service.refresh_runtime_model_locked(config)
    assert vars(config) == original


@pytest.mark.parametrize(
    "invalid",
    [
        "other_config_author",
        "other_binding_author",
        "disabled_config",
        "deleted_config",
        "different_capability",
        "unsupported_format",
        "unknown_protocol",
    ],
)
def test_refresh_cannot_revive_or_rebind_unavailable_saved_models(invalid):
    service, config, channel, profile, binding, _ = runtime_catalog()
    if invalid == "other_config_author":
        config.owner_user_id = 99
    elif invalid == "other_binding_author":
        binding.user_id = 99
    elif invalid == "disabled_config":
        config.enabled = 0
    elif invalid == "deleted_config":
        config.is_deleted = 1
    elif invalid == "different_capability":
        config.service_type = "image"
    elif invalid == "unsupported_format":
        config.runtime_profile["api_format"] = "claude"
    else:
        config.runtime_profile["protocol"] = "plugin/not-implemented"
    original = deepcopy(vars(config))
    with pytest.raises((NotFound, WorkflowError)):
        service.refresh_runtime_model_locked(config)
    assert vars(config) == original


@pytest.mark.parametrize("legacy_change", ["disabled_channel", "removed_model", "missing_profile"])
def test_cutover_legacy_catalog_no_longer_controls_host_runtime(legacy_change):
    service, config, channel, _, _, _ = runtime_catalog()
    if legacy_change == "disabled_channel":
        channel["enabled"] = False
    elif legacy_change == "removed_model":
        channel["models"] = []
    else:
        channel["modelProfiles"] = []
    config.model_key = "host-edited-model"
    assert service.refresh_runtime_model_locked(config) is True
    assert config.model_key == "host-edited-model"
    assert select_adapter(snapshot(config)) == "canvas_openai_videos.v1"


@pytest.mark.parametrize("scene", ["canvas_node", "canvas_model_test", "shot_video"])
@pytest.mark.parametrize("captured_version", ["6", "7"])
def test_old_result_cannot_replace_upgraded_canvas_cache_but_standard_policy_is_retained(
    monkeypatch, scene, captured_version
):
    _, config, _, _, _, _ = runtime_catalog(cache={"adapter": "canvas_openai_videos.v1"})
    frozen = snapshot(config)
    frozen["row_version"] = captured_version
    call = SimpleNamespace(
        id=27,
        config_id=config.id,
        config_snapshot=deepcopy(frozen),
        request_data={"source": {"scene": scene}},
        response_data={},
        provider_task_id=None,
    )
    task = SimpleNamespace(id=37, service_type="video", started_at=utcnow())
    session = SimpleNamespace(
        get=lambda model, identifier: call if model is AIGenerationRecord else config
    )

    @contextmanager
    def begin():
        yield session

    monkeypatch.setattr(
        "short_drama.service.generation_execution_service.owned_task",
        lambda *args: task,
    )
    monkeypatch.setattr(
        "short_drama.service.generation_execution_service.schedule", lambda *args: None
    )
    worker = object.__new__(GenerationExecutionService)
    worker.factory = SimpleNamespace(begin=begin)
    worker.settings = SimpleNamespace(generation_poll_seconds=3)
    before = deepcopy(config.capability_cache)
    worker._store_result(
        task,
        call,
        1,
        "lease",
        GenerationResult("submitted", "ark_video.v1", provider_task_id="original-provider-task"),
    )
    should_write = scene == "shot_video" or captured_version == str(config.row_version)
    assert config.capability_cache["adapter"] == (
        "ark_video.v1" if should_write else before["adapter"]
    )
    assert call.config_snapshot == frozen
    assert call.provider_task_id == "original-provider-task" and call.status == "sent"
    assert session.get(AIModelConfig, config.id) is config


def test_old_standard_result_cannot_reintroduce_cache_after_header_account_change(monkeypatch):
    _, config, _, _, _, _ = runtime_catalog(cache={"adapter": "canvas_openai_videos.v1"})
    config.runtime_credentials_cipher = "old-header-account-envelope"
    frozen = snapshot(config)
    config.runtime_credentials_cipher = "new-header-account-envelope"
    call = SimpleNamespace(
        id=27,
        config_id=config.id,
        config_snapshot=deepcopy(frozen),
        request_data={"source": {"scene": "shot_video"}},
        response_data={},
        provider_task_id=None,
    )
    task = SimpleNamespace(id=37, service_type="video", started_at=utcnow())
    session = SimpleNamespace(
        get=lambda model, identifier: call if model is AIGenerationRecord else config
    )

    @contextmanager
    def begin():
        yield session

    monkeypatch.setattr(
        "short_drama.service.generation_execution_service.owned_task", lambda *args: task
    )
    monkeypatch.setattr(
        "short_drama.service.generation_execution_service.schedule", lambda *args: None
    )
    worker = object.__new__(GenerationExecutionService)
    worker.factory = SimpleNamespace(begin=begin)
    worker.settings = SimpleNamespace(generation_poll_seconds=3)
    before = deepcopy(config.capability_cache)
    worker._store_result(
        task,
        call,
        1,
        "lease",
        GenerationResult("submitted", "ark_video.v1", provider_task_id="original-provider-task"),
    )
    assert config.capability_cache == before
    assert call.config_snapshot == frozen and call.status == "sent"
