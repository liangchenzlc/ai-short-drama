"""真实 MySQL 验证旧目录切换宿主后的真值、加密、作用域与偏好 CAS。"""

import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_canvas_workspace import actor

from short_drama.ai import select_adapter
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_workspace_dao import CanvasWorkspaceDAO
from short_drama.domain import AIModelConfig, CanvasChannelModel, CanvasModelCatalog
from short_drama.schemas.canvas_model_catalog import CanvasModelChannelInput
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.canvas_model_catalog_service import CanvasModelCatalogService
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService
from short_drama.service.model_runtime_config import decrypt_runtime_credentials

pytestmark = pytest.mark.integration
CIPHER = KeyCipher(base64.b64encode(b"c" * 32).decode())


def channel(**changes):
    return {
        "id": "custom-provider",
        "name": "我的服务",
        "baseUrl": "https://provider.example/v1",
        "apiKey": "synthetic-catalog-key",
        "apiFormat": "openai",
        "models": ["gpt-image-1"],
        "modelProfiles": [
            {
                "model": "gpt-image-1",
                "displayName": "图片模型",
                "capability": "image",
                "protocol": "openai-image",
            }
        ],
        **changes,
    }


def save(session, channels, version="0", preferences=None):
    """内部旧数据夹具；返回内部 channels，不能代替公共 HTTP 合同验收。"""
    session.info["actor"] = actor()
    with session.begin():
        CanvasWorkspaceDAO(session).lock_user(1)
        service = CanvasModelCatalogService(session, cipher=CIPHER)
        service.save_locked([CanvasModelChannelInput.model_validate(item) for item in channels])
        session.flush()
        catalog = session.scalar(select(CanvasModelCatalog))
        legacy_channels = deepcopy(catalog.channels_json) if catalog else []
    result = CanvasWorkspaceService(session, cipher=CIPHER).read_models()
    if preferences is not None:
        result = save_preferences(session, preferences, version)
    return {**result, "channels": legacy_channels}


def save_preferences(session, preferences, version="0"):
    session.info["actor"] = actor()
    return CanvasWorkspaceService(session, cipher=CIPHER).save_preferences(
        CanvasWorkspacePreferencesRequest(expected_row_version=version, preferences=preferences)
    )


def model_id(seeded):
    item = next(item for item in seeded["channels"] if item["id"] == "custom-provider")
    return item["modelProfiles"][0]["logicalModelId"]


def test_catalog_encryption_redaction_and_stable_host_projection(db_session):
    identifier = model_id(
        save(
            db_session,
            [
                channel(
                    secretKey="synthetic-secret",
                    headers=[{"name": "X-Key", "value": "hidden"}],
                )
            ],
        )
    )
    public = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    assert public["channels"] == [] and len(public["models"]) == 1
    model = public["models"][0]
    assert model["id"] == identifier and isinstance(identifier, str) and len(identifier) >= 16
    assert model["has_secret_key"] and model["headers"] == [{"name": "X-Key", "has_value": True}]
    assert model["selection_aliases"] == ["custom-provider::gpt-image-1"]
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(identifier))
        catalog = db_session.scalar(select(CanvasModelCatalog))
        binding = db_session.scalar(select(CanvasChannelModel))
        assert binding.runtime_migrated_at is not None
        assert "synthetic" not in json.dumps(catalog.channels_json)
        assert "synthetic" not in catalog.credentials_cipher
        assert CIPHER.decrypt(config.apikey) == "synthetic-catalog-key"
        assert decrypt_runtime_credentials(config, CIPHER) == {
            "secretKey": "synthetic-secret",
            "headers": {"X-Key": "hidden"},
        }
        assert config.row_version == 1
    assert "synthetic" not in json.dumps(public) and "hidden" not in json.dumps(public)


@pytest.mark.parametrize("declared", [{"vision": True, "thinking": ["off", "on"]}, None])
def test_host_text_capability_is_refreshed_and_versioned_without_model_name_inference(
    db_session, declared
):
    profile = {"model": "gpt-4o", "capability": "text", "protocol": "chat-completion"}
    if declared is not None:
        profile["capabilityConfig"] = {"text": deepcopy(declared)}
    identifier = int(
        model_id(save(db_session, [channel(models=["gpt-4o"], modelProfiles=[profile])]))
    )
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier)
        expected = deepcopy(config.capability_cache)
        assert expected.get("canvas_text_capability") == declared
        if declared is None:
            assert "canvas_text_capability" not in expected
        config.capability_cache = None
    original = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    assert CanvasWorkspaceService(db_session, cipher=CIPHER).read_models() == original
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier, populate_existing=True)
        service = CanvasModelCatalogService(db_session, cipher=CIPHER)
        assert service.refresh_runtime_model_locked(config)
        assert config.capability_cache == expected and config.row_version == 2
        assert not service.refresh_runtime_model_locked(config)
    runtime = deepcopy(original["models"][0]["runtime_profile"])
    runtime["capability_config"] = {"text": {"vision": False}}
    changed = AIModelConfigService(db_session, cipher=CIPHER).update(
        identifier, {"row_version": "2", "runtime_profile": runtime}
    )
    assert changed.row_version == 3
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier, populate_existing=True)
        assert config.capability_cache is None
        CanvasModelCatalogService(db_session, cipher=CIPHER).refresh_runtime_model_locked(config)
        assert config.capability_cache["canvas_text_capability"] == {"vision": False}
        assert config.row_version == 4


def test_legacy_catalog_cannot_override_delete_or_revive_host_bound_model(db_session):
    identifier = model_id(save(db_session, [channel()]))
    service = AIModelConfigService(db_session, cipher=CIPHER)
    service.update(identifier, {"row_version": "1", "name": "宿主改名", "runtime_profile": None})
    save(db_session, [channel(apiKey="must-not-be-written")])
    save(db_session, [])
    current = service.get(identifier)
    assert current.name == "宿主改名" and current.runtime_profile is None
    assert current.row_version == 2 and current.enabled
    service.delete(identifier, current.row_version)
    save(db_session, [channel()])
    with pytest.raises(NotFound):
        service.get(identifier)
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(identifier))
        assert config.is_deleted and config.row_version == 3
        assert CIPHER.decrypt(config.apikey) == "synthetic-catalog-key"


def test_catalog_ignores_forged_model_identity_and_isolates_other_account(db_session):
    forged = channel()
    forged["modelProfiles"][0]["logicalModelId"] = "999999999999999999"
    assert model_id(save(db_session, [forged])) != forged["modelProfiles"][0]["logicalModelId"]
    with Session(db_session.get_bind()) as other:
        other.info["actor"] = actor(999)
        result = CanvasWorkspaceService(other).read_models()
        assert result["models"] == result["channels"] == []
        with other.begin():
            assert list(other.scalars(select(CanvasChannelModel))) == []
            assert list(other.scalars(select(CanvasModelCatalog))) == []


@pytest.mark.parametrize("channels", [None, [], [channel()]])
def test_public_channel_writes_are_rejected_atomically_even_when_empty_or_null(
    db_session, channels
):
    save(db_session, [channel()])
    initial = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    with pytest.raises(WorkflowError) as caught:
        CanvasWorkspaceService(db_session, cipher=CIPHER).save_preferences(
            CanvasWorkspacePreferencesRequest(
                expected_row_version="0", preferences={"size": "16:9"}, channels=channels
            )
        )
    assert caught.value.code == "canvas_model_catalog_read_only"
    assert CanvasWorkspaceService(db_session, cipher=CIPHER).read_models() == initial


def test_stale_preference_save_rolls_back_and_never_modifies_host_models(db_session):
    save(db_session, [channel()])
    initial = save_preferences(db_session, {"size": "16:9"})
    with pytest.raises(WorkflowError) as error:
        save_preferences(db_session, {"size": "1:1"}, "0")
    assert error.value.code == "canvas_model_preferences_conflict"
    assert CanvasWorkspaceService(db_session, cipher=CIPHER).read_models() == initial


def test_concurrent_preference_cas_allows_only_one_complete_snapshot(db_session):
    save(db_session, [channel()])
    initial = save_preferences(db_session, {"size": "16:9"})

    def write(size):
        with Session(db_session.get_bind()) as session:
            try:
                return save_preferences(session, {"size": size}, initial["row_version"])[
                    "preferences"
                ]["size"]
            except WorkflowError as error:
                assert error.status_code == 409
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["4:3", "9:16"]))
    assert results.count("conflict") == 1
    result = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    assert result["row_version"] == "2" and result["preferences"]["size"] in results
    assert result["models"] == initial["models"]


def test_host_explicit_protocol_selects_exact_adapter_and_unknown_is_rejected(db_session):
    identifier = int(
        model_id(
            save(
                db_session,
                [
                    channel(
                        modelProfiles=[
                            {
                                "model": "gpt-image-1",
                                "capability": "image",
                                "protocol": "volcengine-ark-image",
                            }
                        ]
                    )
                ],
            )
        )
    )
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier)
        snapshot = {
            key: getattr(config, key)
            for key in ("base_url", "model_key", "service_type", "capability_cache")
        }
        snapshot["credential_identity"] = hashlib.sha256(config.apikey.encode()).hexdigest()
        assert select_adapter(snapshot) == "ark_images.v1"
        CanvasModelCatalogService(db_session).require_protocol_locked(identifier, "ark_images.v1")
    AIModelConfigService(db_session, cipher=CIPHER).update(
        identifier,
        {
            "row_version": "1",
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": "plugin/unknown"},
        },
    )
    with db_session.begin(), pytest.raises(WorkflowError) as error:
        CanvasModelCatalogService(db_session).require_protocol_locked(
            identifier, "openai_images.v1"
        )
    assert error.value.code == "canvas_generation_protocol_unsupported"


def test_host_explicit_clear_removes_credentials_without_reverting_legacy_truth(db_session):
    identifier = model_id(save(db_session, [channel(secretKey="private-secret")]))
    AIModelConfigService(db_session, cipher=CIPHER).update(
        identifier, {"row_version": "1", "apikey": None, "secret_key": None, "headers": []}
    )
    save(db_session, [channel()])
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(identifier))
        assert config.apikey is None and config.runtime_credentials_cipher is None
        assert config.row_version == 2


def test_host_long_model_identity_and_rename_increment_version_once(db_session):
    model_key = "model-" + "a" * 249
    identifier = model_id(
        save(
            db_session,
            [
                channel(
                    models=[model_key],
                    modelProfiles=[
                        {"model": model_key, "capability": "image", "protocol": "openai-image"}
                    ],
                )
            ],
        )
    )
    payload = {"row_version": "1", "name": "改名", "apikey": "new-host-only-key"}
    service = AIModelConfigService(db_session, cipher=CIPHER)
    changed = service.update(identifier, payload)
    assert changed.model_key == model_key and changed.name == "改名" and changed.row_version == 2
    assert service.update(identifier, {**payload, "row_version": "2"}).row_version == 2


def test_internal_catalog_budget_failure_preserves_host_configs_and_preferences(
    db_session, monkeypatch
):
    from short_drama.service import canvas_model_catalog_service

    save(db_session, [channel()])
    before = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    monkeypatch.setattr(canvas_model_catalog_service, "MAX_CATALOG_BYTES", 100)
    with pytest.raises(WorkflowError) as error:
        save(db_session, [channel(apiKey="must-not-be-written")])
    assert error.value.code == "canvas_model_catalog_too_large"
    assert CanvasWorkspaceService(db_session, cipher=CIPHER).read_models() == before
