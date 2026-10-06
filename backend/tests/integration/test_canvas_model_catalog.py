"""真实 MySQL 验证渠道目录、密钥、版本及执行模型身份。"""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_canvas_workspace import actor

from short_drama.ai import select_adapter
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AIModelConfig, CanvasChannelModel, CanvasModelCatalog
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest
from short_drama.service.canvas_model_catalog_service import CanvasModelCatalogService
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService

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
    session.info["actor"] = actor()
    return CanvasWorkspaceService(session, cipher=CIPHER).save_preferences(
        CanvasWorkspacePreferencesRequest(
            expected_row_version=version, preferences=preferences or {}, channels=channels
        )
    )


def test_catalog_encryption_redaction_and_stable_binding(db_session):
    result = save(
        db_session,
        [
            channel(
                secretKey="synthetic-secret",
                headers=[{"name": "X-Provider-Key", "value": "synthetic-header"}],
            )
        ],
    )
    item = result["channels"][0]
    assert result["row_version"] == "1"
    assert item["apiKey"] == item["secretKey"] == item["headers"][0]["value"] == ""
    assert item["hasApiKey"] and item["hasSecretKey"]
    identifier = item["modelProfiles"][0]["logicalModelId"]
    assert isinstance(identifier, str) and len(identifier) >= 16
    assert result["models"] == [], "bound models must not be duplicated as host channels"
    with db_session.begin():
        catalog = db_session.scalar(select(CanvasModelCatalog))
        config = db_session.scalar(select(AIModelConfig).where(AIModelConfig.id == int(identifier)))
        assert "synthetic" not in json.dumps(catalog.channels_json)
        assert "synthetic" not in catalog.credentials_cipher
        assert CIPHER.decrypt(config.apikey) == "synthetic-catalog-key"
        stored = json.loads(CIPHER.decrypt(catalog.credentials_cipher))
        assert stored["custom-provider"]["headers"]["X-Provider-Key"] == "synthetic-header"
        ciphertext = config.apikey
        assert config.row_version == 1
    replay = save(db_session, [item], "1")
    assert replay["row_version"] == "1", "redacted save must preserve credentials and be a no-op"
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(identifier))
        assert config.apikey == ciphertext and config.row_version == 1


@pytest.mark.parametrize("declared", [{"vision": True, "thinking": ["off", "on"]}, None])
def test_text_capability_is_saved_refreshed_and_versioned_without_model_name_inference(
    db_session, declared
):
    profile = {"model": "gpt-4o", "capability": "text", "protocol": "chat-completion"}
    if declared is not None:
        profile["capabilityConfig"] = {"text": deepcopy(declared)}
    result = save(db_session, [channel(models=["gpt-4o"], modelProfiles=[profile])])
    item = result["channels"][0]
    identifier = int(item["modelProfiles"][0]["logicalModelId"])
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier)
        expected = deepcopy(config.capability_cache)
        assert expected.get("canvas_text_capability") == declared
        if declared is None:
            assert "canvas_text_capability" not in expected
        assert config.row_version == 1
        config.capability_cache = None
    assert CanvasWorkspaceService(db_session, cipher=CIPHER).read_models() == result
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier, populate_existing=True)
        service = CanvasModelCatalogService(db_session, cipher=CIPHER)
        assert config.capability_cache is None
        assert service.refresh_runtime_model_locked(config)
        assert config.capability_cache == expected and config.row_version == 2
        assert not service.refresh_runtime_model_locked(config)
        assert config.row_version == 2

    unchanged = save(db_session, [item], result["row_version"])
    assert unchanged == result
    with db_session.begin():
        assert db_session.get(AIModelConfig, identifier).row_version == 2
    edited = deepcopy(item)
    edited["modelProfiles"][0]["capabilityConfig"] = {"text": {"vision": False}}
    changed = save(db_session, [edited], result["row_version"])
    assert changed["row_version"] == "2"
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier)
        assert config.capability_cache["canvas_text_capability"] == {"vision": False}
        assert config.row_version == 3
    assert save(db_session, [changed["channels"][0]], "2") == changed
    with db_session.begin():
        assert db_session.get(AIModelConfig, identifier).row_version == 3


def test_catalog_update_remove_and_readd_keeps_model_identity(db_session):
    initial = save(db_session, [channel()])
    identifier = initial["channels"][0]["modelProfiles"][0]["logicalModelId"]
    edited = deepcopy(initial["channels"][0])
    edited["name"] = "已重命名"
    edited["modelProfiles"][0]["description"] = "原版说明字段"
    assert save(db_session, [edited], "1")["row_version"] == "2"
    assert save(db_session, [], "2")["row_version"] == "3"
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(identifier))
        assert config.is_deleted == 1 and not config.enabled
    revived = save(db_session, [channel()], "3")
    assert revived["channels"][0]["modelProfiles"][0]["logicalModelId"] == identifier
    with db_session.begin():
        assert db_session.get(AIModelConfig, int(identifier)).is_deleted == 0


def test_catalog_ignores_forged_model_identity_and_isolates_other_account(db_session):
    forged = channel()
    forged["modelProfiles"][0]["logicalModelId"] = "999999999999999999"
    result = save(db_session, [forged])
    assert (
        result["channels"][0]["modelProfiles"][0]["logicalModelId"]
        != forged["modelProfiles"][0]["logicalModelId"]
    )
    with Session(db_session.get_bind()) as other:
        other.info["actor"] = actor(999)
        channels = CanvasWorkspaceService(other).read_models()["channels"]
        assert len(channels) == 1 and channels[0]["id"] == "beefapi"
        assert channels[0]["models"] == [] and not channels[0]["hasApiKey"]
        with other.begin():
            assert list(other.scalars(select(CanvasChannelModel))) == []
            assert list(other.scalars(select(CanvasModelCatalog))) == []


def test_catalog_stale_save_rolls_back_all_model_and_preference_changes(db_session):
    initial = save(db_session, [channel()])
    edited = channel(apiKey="must-not-be-written")
    with pytest.raises(WorkflowError) as error:
        save(db_session, [edited], "0", {"size": "16:9"})
    assert error.value.status_code == 409
    assert CanvasWorkspaceService(db_session).read_models() == initial


def test_catalog_concurrent_cas_allows_only_one_complete_snapshot(db_session):
    save(db_session, [channel()])
    engine = db_session.get_bind()

    def write(name):
        with Session(engine) as session:
            try:
                return save(session, [channel(name=name)], "1")["channels"][0]["name"]
            except WorkflowError as error:
                assert error.status_code == 409
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["并发一", "并发二"]))
    assert results.count("conflict") == 1
    result = CanvasWorkspaceService(db_session).read_models()
    assert result["row_version"] == "2" and result["channels"][0]["name"] in results


def test_catalog_explicit_protocol_selects_exact_adapter_and_unknown_is_rejected(db_session):
    result = save(
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
    identifier = int(result["channels"][0]["modelProfiles"][0]["logicalModelId"])
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier)
        import hashlib

        snapshot = {
            "base_url": config.base_url,
            "model_key": config.model_key,
            "service_type": config.service_type,
            "capability_cache": config.capability_cache,
            "credential_identity": hashlib.sha256(config.apikey.encode()).hexdigest(),
        }
        assert select_adapter(snapshot) == "ark_images.v1"
        CanvasModelCatalogService(db_session).require_protocol_locked(identifier, "ark_images.v1")
    edited = deepcopy(result["channels"][0])
    edited["modelProfiles"][0]["protocol"] = "plugin/unknown"
    save(db_session, [edited], "1")
    with db_session.begin(), pytest.raises(WorkflowError) as error:
        CanvasModelCatalogService(db_session).require_protocol_locked(
            identifier, "openai_images.v1"
        )
    assert error.value.code == "canvas_generation_protocol_unsupported"


def test_catalog_explicit_clear_removes_encrypted_credentials(db_session):
    result = save(db_session, [channel()])
    edited = result["channels"][0]
    edited["clearCredentials"] = ["apiKey", "secretKey", "headers"]
    result = save(db_session, [edited], "1")
    assert not result["channels"][0]["hasApiKey"]
    with db_session.begin():
        config = db_session.get(AIModelConfig, int(edited["modelProfiles"][0]["logicalModelId"]))
        assert config.apikey is None


def test_catalog_model_key_preserves_long_identity_and_updates_version_once(db_session):
    model_key = "model-" + "a" * 249
    first = channel(
        models=[model_key],
        modelProfiles=[{"model": model_key, "capability": "image", "protocol": "openai-image"}],
    )
    saved = save(db_session, [first])
    identifier = int(saved["channels"][0]["modelProfiles"][0]["logicalModelId"])
    edited = deepcopy(saved["channels"][0])
    edited["modelProfiles"][0]["displayName"] = "改名"
    edited["apiKey"] = "new-catalog-only-key"
    save(db_session, [edited], "1")
    with db_session.begin():
        config = db_session.get(AIModelConfig, identifier)
        assert config.model_key == model_key and config.name == "改名"
        assert config.row_version == 2


def test_catalog_merged_saved_credentials_obey_budget_and_roll_back(db_session, monkeypatch):
    from short_drama.service import canvas_model_catalog_service

    saved = save(db_session, [channel()])
    edited = deepcopy(saved["channels"][0])
    edited["name"] = "不能提交的新标题"
    monkeypatch.setattr(canvas_model_catalog_service, "MAX_CATALOG_BYTES", 100)
    with pytest.raises(WorkflowError) as error:
        save(db_session, [edited], "1")
    assert error.value.code == "canvas_model_catalog_too_large"
    assert CanvasWorkspaceService(db_session).read_models() == saved
