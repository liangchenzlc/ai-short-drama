"""托管 BeefAPI 内部目录与宿主统一模型的真实 MySQL 身份、同步和默认规则。"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_canvas_model_catalog import CIPHER, channel, save
from test_canvas_workspace import actor

from short_drama.core.exceptions import BusinessError, Conflict, WorkflowError
from short_drama.dao.canvas_workspace_dao import CanvasWorkspaceDAO
from short_drama.domain import AIModelConfig, CanvasModelCatalog
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.canvas_model_catalog_service import CanvasModelCatalogService
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService

pytestmark = pytest.mark.integration
CREDENTIAL = {
    "apiKey": "beefapi-catalog-private-key",
    "baseUrl": "https://enterprise.beefapi.com",
    "accountId": "42",
    "tokenId": "99",
}


def apply(session, models, *, account_changed=False, authorization_id="99"):
    session.info["actor"] = actor()
    with session.begin():
        CanvasWorkspaceDAO(session).lock_user(1)
        CanvasModelCatalogService(session, cipher=CIPHER).apply_beefapi_catalog_locked(
            models,
            CREDENTIAL,
            account_changed=account_changed,
            authorization_id=authorization_id,
        )
    return with_internal_channels(session)


def with_internal_channels(session):
    result = CanvasWorkspaceService(session, cipher=CIPHER).read_models()
    assert result["channels"] == [], "公开模型目录只能由宿主提供"
    with session.begin():
        result["channels"] = CanvasModelCatalogService(session, cipher=CIPHER).read_locked()
    return result


def save_preferences(session, result, values):
    return CanvasWorkspaceService(session, cipher=CIPHER).save_preferences(
        CanvasWorkspacePreferencesRequest(
            expected_row_version=result["row_version"], preferences=values
        )
    )


def managed(result):
    return next(item for item in result["channels"] if item["id"] == "beefapi")


def test_builtin_is_public_server_owned_and_other_accounts_have_no_private_models(db_session):
    db_session.info["actor"] = actor()
    empty = with_internal_channels(db_session)
    item = managed(empty)
    assert item["pinned"] and item["baseUrl"] == "https://enterprise.beefapi.com"
    assert item["models"] == [] and not item["hasApiKey"]
    initial = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    assert managed(initial)["hasApiKey"]
    public = next(item for item in initial["models"] if item["credential_source"] == "beefapi")
    assert public["has_api_key"] and public["id"].isdecimal()
    assert public["selection_aliases"] == ["beefapi::gpt-6-astra"]
    assert CREDENTIAL["apiKey"] not in json.dumps(initial)
    with Session(db_session.get_bind()) as other:
        other.info["actor"] = actor(999)
        assert managed(with_internal_channels(other)) == item
        assert CanvasWorkspaceService(other, cipher=CIPHER).read_models()["models"] == []
        with other.begin():
            assert list(other.scalars(select(CanvasModelCatalog))) == []


def test_catalog_initializes_astra_once_and_refresh_keeps_user_selection(db_session):
    initial = apply(
        db_session,
        [{"id": "gpt-6-astra", "modelType": "text"}, {"id": "other", "modelType": "text"}],
    )
    assert initial["preferences"]["assistantModel"] == "beefapi::gpt-6-astra"
    assert "_assistantAuthorizationId" not in managed(initial)
    identifier = managed(initial)["modelProfiles"][0]["logicalModelId"]
    changed = save_preferences(db_session, initial, {"assistantModel": "beefapi::other"})
    refreshed = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    assert refreshed["preferences"]["assistantModel"] == "beefapi::other"
    assert managed(refreshed)["modelProfiles"][0]["logicalModelId"] == identifier
    assert refreshed["row_version"] == changed["row_version"], "identical refresh is a no-op"
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(identifier))
        catalog = db_session.scalar(select(CanvasModelCatalog))
        assert CIPHER.decrypt(config.apikey) == CREDENTIAL["apiKey"]
        assert catalog.credentials_cipher is None, "managed credentials live in connection state"
        assert CREDENTIAL["apiKey"] not in str(catalog.channels_json)


def test_fresh_missing_astra_cannot_initialize_from_stale_merged_catalog(db_session):
    initial = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}], authorization_id=None)
    assert "assistantModel" not in initial["preferences"]
    stale = apply(db_session, [{"id": "other", "modelType": "text"}], authorization_id="new")
    assert "gpt-6-astra" in managed(stale)["models"]
    assert "assistantModel" not in stale["preferences"]
    fresh = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}], authorization_id="new")
    assert "assistantModel" not in fresh["preferences"], (
        "same authorization cannot reset a later user choice"
    )
    next_authorization = apply(
        db_session, [{"id": "gpt-6-astra", "modelType": "text"}], authorization_id="newer"
    )
    assert next_authorization["preferences"]["assistantModel"] == "beefapi::gpt-6-astra"


def test_same_account_merges_and_changed_account_replaces_while_custom_channels_survive(db_session):
    original = save(db_session, [channel()])
    custom = original["models"][0]
    initial = apply(db_session, [{"id": "old", "modelType": "image"}], authorization_id=None)
    old_id = managed(initial)["modelProfiles"][0]["logicalModelId"]
    merged = apply(db_session, [{"id": "new", "modelType": "text"}], authorization_id=None)
    assert managed(merged)["models"] == ["old", "new"]
    replaced = apply(
        db_session,
        [{"id": "new", "modelType": "text"}],
        account_changed=True,
        authorization_id=None,
    )
    assert managed(replaced)["models"] == ["new"]
    assert next(item for item in replaced["models"] if item["id"] == custom["id"]) == custom
    with db_session.begin():
        old = db_session.get(AIModelConfig, int(old_id))
        assert old.is_deleted and not old.enabled


def test_public_save_cannot_replace_managed_profile_origin_or_identity_and_cas_conflicts(
    db_session,
):
    initial = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    identifier = managed(initial)["modelProfiles"][0]["logicalModelId"]
    service = AIModelConfigService(db_session, cipher=CIPHER)
    current = service.get(identifier)
    for forged in (
        {"base_url": "https://evil.example"},
        {"model_key": "fake"},
        {"runtime_profile": None},
        {"apikey": "forged"},
        {"secret_key": "forged"},
    ):
        with pytest.raises(BusinessError):
            service.update(identifier, {"row_version": current.row_version, **forged})
    with pytest.raises(WorkflowError, match="模型配置已统一"):
        CanvasWorkspaceService(db_session, cipher=CIPHER).save_preferences(
            CanvasWorkspacePreferencesRequest(
                expected_row_version=initial["row_version"], preferences={}, channels=[]
            )
        )
    updated = service.update(
        identifier, {"row_version": current.row_version, "name": "本人名称", "enabled": 0}
    )
    with pytest.raises(Conflict):
        service.update(identifier, {"row_version": current.row_version, "enabled": 1})
    apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    refreshed = service.get(identifier)
    assert refreshed.name == "本人名称" and not refreshed.enabled
    assert refreshed.model_key == current.model_key and refreshed.base_url == current.base_url
    assert refreshed.row_version >= updated.row_version
    service.delete(identifier, refreshed.row_version)
    result = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    assert not any(item["id"] == identifier for item in result["models"])


def test_clear_disables_only_managed_configs_and_keeps_empty_builtin(db_session):
    custom = save(db_session, [channel()])["models"][0]
    initial = apply(
        db_session,
        [{"id": "whisper", "modelType": "audio"}, {"id": "gpt-6-astra", "modelType": "text"}],
    )
    item = managed(initial)
    assert item["modelProfiles"][0]["capability"] == ""
    assert "logicalModelId" not in item["modelProfiles"][0]
    identifier = item["modelProfiles"][1]["logicalModelId"]
    with db_session.begin():
        CanvasWorkspaceDAO(db_session).lock_user(1)
        CanvasModelCatalogService(db_session, cipher=CIPHER).clear_beefapi_locked()
    result = with_internal_channels(db_session)
    empty = managed(result)
    assert empty["models"] == empty["modelProfiles"] == []
    assert empty["pinned"] and not empty["hasApiKey"] and "credentialRef" not in empty
    assert next(item for item in result["models"] if item["id"] == custom["id"]) == custom
    with db_session.begin():
        assert db_session.get(AIModelConfig, int(identifier)).is_deleted
        assert not db_session.get(AIModelConfig, int(identifier)).enabled


def test_absent_managed_catalog_cannot_be_forged_by_a_custom_save(db_session):
    db_session.info["actor"] = actor()
    empty = managed(with_internal_channels(db_session))
    empty.update(baseUrl="https://evil.example", models=["forged"])
    empty["modelProfiles"] = [
        {"model": "forged", "capability": "image", "protocol": "openai-image"}
    ]
    with pytest.raises(WorkflowError, match="模型配置已统一"):
        CanvasWorkspaceService(db_session, cipher=CIPHER).save_preferences(
            CanvasWorkspacePreferencesRequest(
                expected_row_version="0", preferences={}, channels=[empty]
            )
        )
    result = with_internal_channels(db_session)
    assert managed(result)["models"] == [] and managed(result)["modelProfiles"] == []
    assert managed(result)["baseUrl"] == CREDENTIAL["baseUrl"]


def test_managed_headers_save_redaction_case_reuse_and_disconnect_retention(db_session):
    initial = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    identifier = managed(initial)["modelProfiles"][0]["logicalModelId"]
    service = AIModelConfigService(db_session, cipher=CIPHER)
    current = service.get(identifier)
    updated = service.update(
        identifier,
        {
            "row_version": current.row_version,
            "headers": [{"name": "X-Provider-Key", "value": "managed-private-header"}],
        },
    )
    assert updated.headers[0].has_value
    assert "managed-private-header" not in updated.model_dump_json()
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(identifier))
        ciphertext = config.runtime_credentials_cipher
        assert "managed-private-header" not in ciphertext
        assert json.loads(CIPHER.decrypt(ciphertext))["headers"] == {
            "X-Provider-Key": "managed-private-header"
        }
    retained = service.update(
        identifier,
        {"row_version": updated.row_version, "headers": [{"name": "x-provider-key", "value": ""}]},
    )
    assert retained.row_version == updated.row_version, "case-normalized redacted header is a no-op"
    apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    with db_session.begin():
        CanvasWorkspaceDAO(db_session).lock_user(1)
        CanvasModelCatalogService(db_session, cipher=CIPHER).clear_beefapi_locked()
    disconnected = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    assert not any(item["credential_source"] == "beefapi" for item in disconnected["models"])
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(identifier))
        assert config.runtime_credentials_cipher == ciphertext
