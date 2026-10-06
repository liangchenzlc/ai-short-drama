import base64
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.ai.canvas_credentials import CanvasCredentials, decode_canvas_credentials
from short_drama.ai.gateway import _authentication
from short_drama.ai.model_identity import model_credential_identity
from short_drama.ai.types import GenerationError
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import ConfigurationError, WorkflowError
from short_drama.schemas.ai_model_config import AIModelConfigCreate, AIModelConfigUpdate
from short_drama.schemas.canvas_model_test import CanvasModelTestCreate
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest
from short_drama.schemas.model_runtime_profile import ModelRuntimeProfile
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.canvas_credential_freeze import freeze_model_credentials
from short_drama.service.canvas_model_catalog_service import CanvasModelCatalogService
from short_drama.service.canvas_model_test_service import CanvasModelTestService
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService
from short_drama.service.model_runtime_config import (
    decrypt_runtime_credentials,
    encrypt_runtime_credentials,
    legacy_runtime_profile,
    refresh_runtime_model,
)


def test_host_runtime_schema_preserves_source_capability_fields_and_secret_redaction():
    profile = {
        "version": 1,
        "api_format": "openai",
        "protocol": "chat-completion",
        "capability_config": {"text": {"references": {"maxImages": 2, "maxImageBytes": 99}}},
        "default_options": {"variants": [{"model": "source-sku"}]},
    }
    value = AIModelConfigCreate(
        service_type="text",
        name="模型",
        model_key="test",
        provider="厂商",
        runtime_profile=profile,
        secret_key="synthetic-secret",
        headers=[{"name": "X-Provider-Key", "value": "synthetic-header"}],
    )
    assert value.runtime_profile.model_dump(exclude_none=True) == profile
    assert "synthetic-secret" not in repr(value)
    assert "synthetic-header" not in repr(value)
    omitted = AIModelConfigUpdate(row_version="1")
    assert "headers" not in omitted.model_fields_set
    assert AIModelConfigUpdate(row_version="1", headers=[]).headers == []


@pytest.mark.parametrize(
    "patch",
    [
        {"version": True},
        {"credential_source": "beefapi"},
        {"capability_config": {"apiKey": "forbidden"}},
        {"capability_config": {"text": {"references": {"maxImages": -1}}}},
        {"capability_config": {"text": {"streaming": "false"}}},
        {"reference_asset_origin": "https://user:password@example.com"},
    ],
)
def test_runtime_profile_rejects_invalid_or_private_fields(patch):
    with pytest.raises(ValidationError):
        ModelRuntimeProfile.model_validate(
            {
                "version": 1,
                "api_format": "openai",
                "protocol": "chat-completion",
                **patch,
            }
        )


def test_runtime_credentials_encrypt_extensions_without_duplicating_api_key():
    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    encrypted = encrypt_runtime_credentials("synthetic-secret", {"X-Test": "hidden"}, cipher)
    assert "hidden" not in encrypted and "synthetic-secret" not in encrypted
    config = SimpleNamespace(runtime_credentials_cipher=encrypted)
    assert decrypt_runtime_credentials(config, cipher) == {
        "secretKey": "synthetic-secret",
        "headers": {"X-Test": "hidden"},
    }
    assert encrypt_runtime_credentials("", {}, cipher) is None


def test_legacy_profile_and_runtime_refresh_keep_identity_and_reject_unknown_protocol():
    channel = {"id": "legacy", "apiFormat": "openai"}
    profile = {
        "model": "test",
        "capability": "text",
        "protocol": "chat-completion",
        "capabilityConfig": {"text": {"references": {"maxImages": 2, "maxImageBytes": 99}}},
    }
    config = SimpleNamespace(
        id=17,
        runtime_profile=legacy_runtime_profile(channel, profile),
        apikey="encrypted-key",
        base_url="https://provider.example/v1",
        model_key="test",
        service_type="text",
        capability_cache=None,
    )
    before = deepcopy(vars(config))
    assert refresh_runtime_model(config, "legacy") is True
    assert config.capability_cache["canvas_text_capability"] == profile["capabilityConfig"]["text"]
    assert all(
        getattr(config, key) == value for key, value in before.items() if key != "capability_cache"
    )
    assert refresh_runtime_model(config, "legacy") is False
    config.runtime_profile["protocol"] = "source-unimplemented-protocol"
    with pytest.raises(GenerationError, match="unsupported_protocol"):
        refresh_runtime_model(config, "legacy")


def test_standard_header_freeze_has_independent_scope_and_preserves_legacy_credentials():
    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    record = SimpleNamespace(
        adapter="openai_chat.v1",
        config_snapshot={"service_type": "text"},
        request_data={"source": {"scene": "novel_script"}},
        credential_cipher="old-key",
    )
    freeze_model_credentials(
        record, {"apiKey": "synthetic-api-key", "headers": {"X-Test": "hidden"}}, cipher
    )
    assert "canvas_auth_version" not in record.config_snapshot
    assert record.config_snapshot["model_auth_scope"] == "generation"
    plaintext = cipher.decrypt(record.credential_cipher)
    credential = decode_canvas_credentials(record.config_snapshot, record.request_data, plaintext)
    assert isinstance(credential, CanvasCredentials)
    _, headers, secrets = _authentication(record.config_snapshot, credential, {})
    assert headers == {"X-Test": "hidden", "Authorization": "Bearer synthetic-api-key"}
    assert set(secrets) == {"hidden", "synthetic-api-key"}
    with pytest.raises(GenerationError, match="invalid_credential"):
        decode_canvas_credentials(
            record.config_snapshot, {"source": {"scene": "canvas_node"}}, plaintext
        )
    assert decode_canvas_credentials({}, {}, "old-key") == "old-key"


def test_runtime_header_changes_preserve_omitted_and_redacted_values_and_clear_explicit_list():
    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    service = object.__new__(AIModelConfigService)
    service.cipher = cipher
    config = SimpleNamespace(
        runtime_credentials_cipher=encrypt_runtime_credentials(
            "synthetic-secret", {"X-Keep": "hidden", "X-Remove": "removed"}, cipher
        )
    )
    omitted = service._runtime_values({}, config)
    assert omitted == {}
    headers = AIModelConfigUpdate(row_version="1", headers=[{"name": "x-keep", "value": ""}])
    changed = service._runtime_values(headers.model_dump(exclude_unset=True), config)
    saved = SimpleNamespace(runtime_credentials_cipher=changed["runtime_credentials_cipher"])
    assert decrypt_runtime_credentials(saved, cipher) == {
        "secretKey": "synthetic-secret",
        "headers": {"X-Keep": "hidden"},
    }
    clear = AIModelConfigUpdate(row_version="1", headers=[], secret_key=None)
    changed = service._runtime_values(clear.model_dump(exclude_unset=True), config)
    assert changed["runtime_credentials_cipher"] is None


@pytest.mark.parametrize(
    "channels",
    [None, [], [{"id": "source", "name": "source", "baseUrl": "", "apiFormat": "openai"}]],
)
def test_canvas_channel_writes_are_rejected_before_starting_a_transaction(channels):
    service = object.__new__(CanvasWorkspaceService)
    payload = CanvasWorkspacePreferencesRequest(
        expected_row_version="0", preferences={}, channels=channels
    )
    with pytest.raises(WorkflowError, match="模型配置已统一"):
        service.save_preferences(payload)


def test_runtime_profile_read_contains_typed_header_presence_and_no_cipher():
    from short_drama.domain import AIModelConfig

    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    entity = AIModelConfig(
        id=17,
        owner_user_id=3,
        name="test",
        model_key="test",
        service_type="text",
        provider="provider",
        base_url="",
        enabled=1,
        is_deleted=0,
        is_default=0,
        row_version=1,
        runtime_credentials_cipher=encrypt_runtime_credentials(
            "synthetic-secret", {"X-Test": "hidden"}, cipher
        ),
    )
    service = AIModelConfigService(SimpleNamespace(scalar=lambda statement: None), cipher=cipher)
    result = service._read(entity)
    assert result.headers[0].has_value is True
    assert result.has_secret_key is True
    assert "cipher" not in result.model_dump_json() and "hidden" not in result.model_dump_json()


@pytest.mark.parametrize("secret", ["a" * 16385, "密" * 5462], ids=["ascii", "utf8"])
def test_runtime_secret_byte_limit_is_validated_before_service_calls(secret):
    with pytest.raises(ValidationError, match="byte limit"):
        AIModelConfigUpdate(row_version="1", secret_key=secret)


def test_explicit_full_clear_repairs_unreadable_extension_without_discarding_omitted_fields():
    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    service = object.__new__(AIModelConfigService)
    service.cipher = cipher
    config = SimpleNamespace(runtime_credentials_cipher="unreadable-envelope")
    payload = AIModelConfigUpdate(row_version="1", secret_key=None, headers=[])
    values = service._runtime_values(payload.model_dump(exclude_unset=True), config)
    assert values["runtime_credentials_cipher"] is None
    for patch in ({"secret_key": None}, {"headers": []}):
        payload = AIModelConfigUpdate(row_version="1", **patch)
        with pytest.raises(ConfigurationError, match="扩展凭据"):
            service._runtime_values(payload.model_dump(exclude_unset=True), config)


def test_redacted_same_header_is_complete_no_op_after_name_normalization():
    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    service = object.__new__(AIModelConfigService)
    service.cipher = cipher
    config = SimpleNamespace(
        runtime_credentials_cipher=encrypt_runtime_credentials("", {"X-Keep": "hidden"}, cipher)
    )
    payload = AIModelConfigUpdate(row_version="1", headers=[{"name": "x-keep", "value": ""}])
    assert service._runtime_values(payload.model_dump(exclude_unset=True), config) == {
        "row_version": 1
    }


@pytest.mark.parametrize("new_value_size", [4088, 4089, 4096])
def test_merged_redacted_header_byte_budget_is_validated_without_mutating_saved_cipher(
    new_value_size,
):
    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    saved_headers = {"X1": "a" * 4096, "X2": "b" * 4096, "X3": "c" * 4096}
    encrypted = encrypt_runtime_credentials("private-secret", saved_headers, cipher)
    config = SimpleNamespace(runtime_credentials_cipher=encrypted)
    service = AIModelConfigService(SimpleNamespace(), cipher=cipher)
    payload = AIModelConfigUpdate(
        row_version="1",
        headers=[{"name": name, "value": ""} for name in saved_headers]
        + [{"name": "X4", "value": "d" * new_value_size}],
    )
    values = payload.model_dump(exclude_unset=True)
    if new_value_size == 4088:
        changed = service._runtime_values(values, config)
        result = decrypt_runtime_credentials(
            SimpleNamespace(runtime_credentials_cipher=changed["runtime_credentials_cipher"]),
            cipher,
        )
        assert result == {
            "secretKey": "private-secret",
            "headers": {**saved_headers, "X4": "d" * new_value_size},
        }
    else:
        with pytest.raises(WorkflowError) as error:
            service._runtime_values(values, config)
        assert error.value.status_code == 422
        assert error.value.code == "model_runtime_credentials_invalid"
        assert "private-secret" not in str(error.value)
    assert config.runtime_credentials_cipher == encrypted
    assert decrypt_runtime_credentials(config, cipher) == {
        "secretKey": "private-secret",
        "headers": saved_headers,
    }


def test_cleared_host_profile_uses_host_key_headers_and_never_reopens_legacy_catalog():
    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    config = SimpleNamespace(
        id=17,
        owner_user_id=3,
        runtime_profile=None,
        runtime_credentials_cipher=encrypt_runtime_credentials("", {"X-Host": "header"}, cipher),
        apikey=cipher.encrypt("host-key"),
        enabled=1,
        is_deleted=0,
        capability_cache=None,
    )
    binding = SimpleNamespace(channel_key="old-channel", user_id=3)
    service = object.__new__(CanvasModelCatalogService)
    service.session = SimpleNamespace(info={"actor": SimpleNamespace(user_id=3)})

    def reject_catalog(*args, **kwargs):
        pytest.fail("cutover must not read old catalog")

    service.catalog_dao = SimpleNamespace(
        binding=lambda identifier: binding,
        models=lambda ids, **kwargs: {17: config},
        catalog=reject_catalog,
    )
    service.configs = AIModelConfigService(service.session, cipher=cipher)
    assert service.refresh_runtime_model_locked(config) is False
    service.require_protocol_locked(17, "openai_chat.v1")
    assert service.runtime_credentials_locked(17, "openai_chat.v1") == {
        "apiKey": "host-key",
        "secretKey": "",
        "headers": {"X-Host": "header"},
    }


@pytest.mark.parametrize(
    "profile", [None, {"version": 1, "api_format": "openai", "protocol": "chat-completion"}]
)
@pytest.mark.parametrize("include_legacy_channel", [False, True])
def test_enterprise_directory_refresh_cannot_modify_or_delete_ordinary_host_model(
    profile, include_legacy_channel
):
    config = SimpleNamespace(
        id=17,
        runtime_profile=profile,
        runtime_credentials_cipher="host-envelope",
        apikey="host-key-envelope",
        model_key="host-edited-model",
        name="host-edited-name",
        enabled=0,
        is_deleted=0,
        row_version=9,
    )
    binding = SimpleNamespace(
        model_config_id=17,
        channel_key="old-channel",
        model_key="old-model",
        runtime_migrated_at=None,
    )
    channel = {
        "id": "old-channel",
        "models": ["old-model"],
        "modelProfiles": [{"model": "old-model", "capability": "text"}],
    }
    stored = SimpleNamespace(channels_json=[deepcopy(channel)], credentials_cipher=None)
    service = object.__new__(CanvasModelCatalogService)
    service.session = SimpleNamespace(info={"actor": SimpleNamespace(user_id=3)}, new=[])
    service.configs = AIModelConfigService(service.session)
    service.catalog_dao = SimpleNamespace(
        catalog=lambda actor, **kwargs: stored,
        bindings=lambda actor, **kwargs: [binding],
        models=lambda ids, **kwargs: {17: config},
    )
    before = deepcopy(vars(config))
    service._persist_locked([channel] if include_legacy_channel else [], {})
    assert vars(config) == before
    assert binding.runtime_migrated_at is None


def test_beefapi_model_test_uses_trusted_temporary_runtime_channel_provenance():
    config = SimpleNamespace(
        id=17,
        model_key="seedance-2.5",
        service_type="video",
        runtime_profile={"version": 1, "api_format": "openai", "protocol": "volcengine-ark-video"},
        capability_cache={"canvas_channel_key": "beefapi"},
    )
    service = object.__new__(CanvasModelCatalogService)
    service.catalog_dao = SimpleNamespace(
        binding=lambda identifier: None, models=lambda ids, **kwargs: {17: config}
    )
    service.require_protocol_locked(17, "canvas_beefapi_seedance_video.v1")
    with pytest.raises(WorkflowError, match="执行适配器不一致"):
        service.require_protocol_locked(17, "ark_video.v1")


@pytest.mark.parametrize("protocol", ["openai-videos", "unknown-source-protocol"])
@pytest.mark.parametrize("extension", [None, "synthetic-extended-envelope"])
def test_capabilities_reads_saved_profile_without_mutating_or_falling_back(protocol, extension):
    entity = SimpleNamespace(
        id=17,
        is_deleted=0,
        model_key="source-model",
        service_type="video",
        base_url="https://provider.example/v1",
        apikey="synthetic-envelope",
        runtime_credentials_cipher=extension,
        runtime_profile={"version": 1, "api_format": "openai", "protocol": protocol},
        capability_cache=None,
        row_version=9,
    )
    service = AIModelConfigService(SimpleNamespace(scalar=lambda statement: None))

    @contextmanager
    def transaction():
        yield

    service._transaction = transaction
    service._require = lambda *args, **kwargs: entity
    before = deepcopy(vars(entity))
    value = service.capabilities(17)
    assert value["known"] is (protocol == "openai-videos")
    assert vars(entity) == before


def test_header_account_identity_changes_without_rewriting_frozen_call_or_reusing_evidence():
    from short_drama.service.agent_model_service import capability_evidence, model_snapshot

    config = SimpleNamespace(
        id=17,
        name="fixture",
        row_version=3,
        service_type="text",
        model_key="fixture",
        provider="fixture",
        base_url="https://provider.example/v1",
        capability_cache={},
        apikey="same-encrypted-api-key",
        runtime_credentials_cipher="header-account-one-envelope",
    )
    frozen = model_snapshot(config)
    config.capability_cache["agent"] = {
        "row_version": 3,
        "model_key": config.model_key,
        "base_url": config.base_url,
        "credential_identity": frozen["credential_identity"],
        "tool_calling": True,
    }
    assert capability_evidence(config)["tool_calling"]
    config.runtime_credentials_cipher = "header-account-two-envelope"
    assert model_credential_identity(config) != frozen["credential_identity"]
    assert capability_evidence(config) == {}
    assert frozen["credential_identity"] != model_snapshot(config)["credential_identity"]


def test_legacy_saved_reference_is_rejected_without_reading_old_catalog():
    service = object.__new__(CanvasModelCatalogService)

    @contextmanager
    def transaction(**kwargs):
        yield

    service._transaction = transaction
    with pytest.raises(WorkflowError, match="旧渠道凭据引用已停用"):
        service.discovery_credentials(
            "old-channel", "host:old-channel", "https://provider.example/v1"
        )
    assert service.discovery_credentials("old-channel", None, "https://provider.example/v1") is None
    payload = CanvasModelTestCreate.model_validate(
        {
            "channel": {
                "id": "old-channel",
                "name": "旧渠道",
                "baseUrl": "https://provider.example/v1",
                "apiFormat": "openai",
                "credentialRef": "host:old-channel",
                "models": ["test"],
                "modelProfiles": [
                    {"model": "test", "capability": "text", "protocol": "chat-completion"}
                ],
            },
            "model": "test",
            "mode": "text",
            "prompt": "test",
            "clientOperationId": "legacy-reference",
        }
    )
    testing = object.__new__(CanvasModelTestService)
    with pytest.raises(WorkflowError, match="旧渠道凭据引用已停用"):
        testing._draft_locked(payload)


def test_host_discovery_uses_row_credentials_with_host_reference_without_channel_id():
    cipher = KeyCipher(base64.b64encode(b"r" * 32).decode())
    config = SimpleNamespace(
        id=17,
        is_deleted=0,
        base_url="https://provider.example/v1",
        apikey=cipher.encrypt("host-key"),
        runtime_credentials_cipher=encrypt_runtime_credentials(
            "", {"X-Host": "host-header"}, cipher
        ),
    )
    service = object.__new__(CanvasModelCatalogService)
    service.configs = AIModelConfigService(SimpleNamespace(), cipher=cipher)
    service.catalog_dao = SimpleNamespace(binding=lambda identifier: None)
    requested = []
    service._require = lambda model, identifier, **kwargs: requested.append(identifier) or config

    @contextmanager
    def transaction(**kwargs):
        yield

    service._transaction = transaction
    assert service.discovery_credentials(None, "host:17", config.base_url) == {
        "apiKey": "host-key",
        "headers": {"X-Host": "host-header"},
    }
    assert requested == ["17"]
