"""托管 BeefAPI 目录的真实 MySQL 原子保存、身份与源默认规则。"""

from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_canvas_model_catalog import CIPHER, channel, save
from test_canvas_workspace import actor

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.canvas_workspace_dao import CanvasWorkspaceDAO
from short_drama.domain import AIModelConfig, CanvasModelCatalog
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest
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
    return CanvasWorkspaceService(session, cipher=CIPHER).read_models()


def managed(result):
    return next(item for item in result["channels"] if item["id"] == "beefapi")


def test_builtin_is_public_server_owned_and_other_accounts_have_no_private_models(db_session):
    db_session.info["actor"] = actor()
    empty = CanvasWorkspaceService(db_session).read_models()
    item = managed(empty)
    assert item["pinned"] and item["baseUrl"] == "https://enterprise.beefapi.com"
    assert item["models"] == [] and not item["hasApiKey"]
    initial = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    assert managed(initial)["hasApiKey"]
    with Session(db_session.get_bind()) as other:
        other.info["actor"] = actor(999)
        assert managed(CanvasWorkspaceService(other).read_models()) == item
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
    changed = save(db_session, [], initial["row_version"], {"assistantModel": "beefapi::other"})
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
    custom = original["channels"][0]
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
    assert next(item for item in replaced["channels"] if item["id"] == custom["id"]) == custom
    with db_session.begin():
        old = db_session.get(AIModelConfig, int(old_id))
        assert old.is_deleted and not old.enabled


def test_public_save_cannot_replace_managed_profile_origin_or_identity_and_cas_conflicts(
    db_session,
):
    initial = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    forged = deepcopy(managed(initial))
    forged.update(baseUrl="https://evil.example", models=["fake"], enabled=False)
    forged["modelProfiles"] = [
        {
            "model": "fake",
            "capability": "video",
            "protocol": "volcengine-ark-video",
            "logicalModelId": "999",
        }
    ]
    result = save(db_session, [forged], initial["row_version"], initial["preferences"])
    item = managed(result)
    assert item["models"] == managed(initial)["models"]
    assert item["modelProfiles"] == managed(initial)["modelProfiles"]
    assert item["baseUrl"] == CREDENTIAL["baseUrl"] and not item["enabled"]
    with pytest.raises(WorkflowError) as caught:
        save(db_session, [], initial["row_version"])
    assert caught.value.status_code == 409
    with db_session.begin():
        identifier = item["modelProfiles"][0]["logicalModelId"]
        assert not db_session.get(AIModelConfig, int(identifier)).enabled


def test_clear_disables_only_managed_configs_and_keeps_empty_builtin(db_session):
    save(db_session, [channel()])
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
    result = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    empty = managed(result)
    assert empty["models"] == empty["modelProfiles"] == []
    assert empty["pinned"] and not empty["hasApiKey"] and "credentialRef" not in empty
    assert any(item["id"] == "custom-provider" for item in result["channels"])
    with db_session.begin():
        assert db_session.get(AIModelConfig, int(identifier)).is_deleted
        assert not db_session.get(AIModelConfig, int(identifier)).enabled


def test_absent_managed_catalog_cannot_be_forged_by_a_custom_save(db_session):
    db_session.info["actor"] = actor()
    empty = managed(CanvasWorkspaceService(db_session).read_models())
    empty.update(baseUrl="https://evil.example", models=["forged"])
    empty["modelProfiles"] = [
        {"model": "forged", "capability": "image", "protocol": "openai-image"}
    ]
    result = CanvasWorkspaceService(db_session, cipher=CIPHER).save_preferences(
        CanvasWorkspacePreferencesRequest(
            expected_row_version="0", preferences={}, channels=[empty]
        )
    )
    assert managed(result)["models"] == [] and managed(result)["modelProfiles"] == []
    assert managed(result)["baseUrl"] == CREDENTIAL["baseUrl"]


def test_managed_headers_save_redaction_case_reuse_and_disconnect_retention(db_session):
    initial = apply(db_session, [{"id": "gpt-6-astra", "modelType": "text"}])
    item = managed(initial)
    item["headers"] = [{"name": "X-Provider-Key", "value": "managed-private-header"}]
    updated = save(db_session, [item], initial["row_version"], initial["preferences"])
    assert managed(updated)["headers"] == [{"name": "X-Provider-Key", "value": ""}]
    with db_session.begin():
        row = db_session.scalar(select(CanvasModelCatalog))
        assert "managed-private-header" not in str(row.channels_json)
        assert "managed-private-header" not in row.credentials_cipher
        credentials = CanvasModelCatalogService(db_session, cipher=CIPHER)._credentials(row)
        assert credentials["beefapi"]["headers"]["X-Provider-Key"] == "managed-private-header"
        assert credentials["beefapi"]["apiKey"] == ""
    replay = managed(updated)
    replay["headers"][0]["name"] = "x-provider-key"
    retained = save(db_session, [replay], updated["row_version"], updated["preferences"])
    assert retained["row_version"] == updated["row_version"], (
        "case-normalized redacted header is a no-op"
    )
    with db_session.begin():
        CanvasWorkspaceDAO(db_session).lock_user(1)
        CanvasModelCatalogService(db_session, cipher=CIPHER).clear_beefapi_locked()
    disconnected = CanvasWorkspaceService(db_session, cipher=CIPHER).read_models()
    assert managed(disconnected)["headers"] == [{"name": "X-Provider-Key", "value": ""}]
    with db_session.begin():
        row = db_session.scalar(select(CanvasModelCatalog))
        stored = CanvasModelCatalogService(db_session, cipher=CIPHER)._credentials(row)
        assert stored["beefapi"]["headers"]["X-Provider-Key"] == "managed-private-header"
