import base64
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.ai.canvas_credentials import CanvasCredentials, decode_canvas_credentials
from short_drama.ai.types import GenerationError
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_model_catalog import CanvasModelChannelInput
from short_drama.service.canvas_credential_freeze import freeze_canvas_credentials


def cipher():
    return KeyCipher(base64.b64encode(b"c" * 32).decode())


def record(scene="canvas_node", adapter="openai_chat.v1"):
    return SimpleNamespace(
        config_snapshot={
            "base_url": "https://provider.example/v1",
            "credential_identity": "original-identity",
        },
        request_data={"source": {"scene": scene}, "input": {"messages": []}},
        credential_cipher="old-cipher",
        adapter=adapter,
    )


def test_freeze_preserves_request_and_host_identity_without_plaintext_secret_json():
    value = record()
    before = deepcopy(value.request_data)
    secrets = {
        "apiKey": "private-main-key",
        "headers": {"X-Waf-Token": "private-header"},
        "secretKey": "unused-private-secret-key",
    }
    freeze_canvas_credentials(value, secrets, cipher())
    assert value.request_data == before
    assert value.config_snapshot["credential_identity"] == "original-identity"
    public = (
        json.dumps(value.config_snapshot) + json.dumps(value.request_data) + value.credential_cipher
    )
    assert not any(
        item in public
        for item in ("private-main-key", "private-header", "unused-private-secret-key")
    )
    plaintext = cipher().decrypt(value.credential_cipher)
    assert "secretKey" not in plaintext and "unused-private-secret-key" not in plaintext
    parsed = decode_canvas_credentials(value.config_snapshot, value.request_data, plaintext)
    assert isinstance(parsed, CanvasCredentials)
    assert parsed.api_key.get_secret_value() == "private-main-key"
    assert parsed.headers[0].value.get_secret_value() == "private-header"
    assert "private-main-key" not in repr(parsed) and "private-header" not in repr(parsed)


def test_largest_supported_header_and_api_key_envelope_fits_existing_text_column():
    value = record()
    freeze_canvas_credentials(
        value,
        {"apiKey": "k" * 16384, "headers": {f"X-{index}": "v" * 4080 for index in range(4)}},
        cipher(),
    )
    assert len(value.credential_cipher.encode()) < 65536


@pytest.mark.parametrize("legacy", ["ordinary-key", '{"version":1,"apiKey":"json-shaped-key"}'])
def test_legacy_plaintext_is_never_guessed_as_json_credentials(legacy):
    assert decode_canvas_credentials({}, {"source": {"scene": "canvas_node"}}, legacy) == legacy


@pytest.mark.parametrize("changes", [{"canvas_auth_version": 2}, {"canvas_auth_scene": "standard"}])
def test_envelope_marker_cannot_escape_canvas_scope(changes):
    value = record()
    freeze_canvas_credentials(value, {"apiKey": "private-main-key", "headers": {}}, cipher())
    with pytest.raises(GenerationError, match="invalid_credential"):
        decode_canvas_credentials(
            {**value.config_snapshot, **changes},
            value.request_data,
            cipher().decrypt(value.credential_cipher),
        )
    with pytest.raises(GenerationError, match="invalid_credential"):
        decode_canvas_credentials(
            value.config_snapshot,
            {"source": {"scene": "standard"}},
            cipher().decrypt(value.credential_cipher),
        )


@pytest.mark.parametrize(
    "scene,adapter",
    [
        ("standard", "openai_chat.v1"),
        ("standard", "modelhub_video.v1"),
        ("canvas_node", "unknown.v1"),
        ("canvas_node", "dashscope_speech.v1"),
        ("canvas_node", "dashscope_voice_design.v1"),
        ("canvas_model_test", "plugin.video.v1"),
    ],
)
def test_freeze_rejects_unimplemented_protocol_or_standard_scope_without_mutating_record(
    scene, adapter
):
    value = record(scene, adapter)
    before = deepcopy(value.__dict__)
    with pytest.raises(WorkflowError):
        freeze_canvas_credentials(value, {"apiKey": "private-main-key", "headers": {}}, cipher())
    assert value.__dict__ == before


@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "Content-Type",
        "Accept",
        "Host",
        "Cookie",
        "Set-Cookie",
        "Connection",
        "Proxy-Connection",
        "Keep-Alive",
        "Transfer-Encoding",
        "TE",
        "Trailer",
        "Upgrade",
        "Forwarded",
        "X-Goog-Api-Key",
        "X-Canvas-Anything",
        "X-Forwarded-For",
    ],
)
def test_source_forbidden_headers_never_enter_encrypted_execution_snapshot(header):
    value = record()
    with pytest.raises(WorkflowError):
        freeze_canvas_credentials(
            value, {"apiKey": "private-main-key", "headers": {header: "private-value"}}, cipher()
        )
    assert value.credential_cipher == "old-cipher"


@pytest.mark.parametrize(
    "headers",
    [
        {f"X-{index}": "v" for index in range(33)},
        {"X-Value": "v" * 4097},
        {"X-Value": "first\r\nInjected:yes"},
        {f"X-{index}": "v" * 4096 for index in range(4)},
    ],
)
def test_source_header_count_value_and_aggregate_limits_are_validated_before_freeze(headers):
    with pytest.raises(WorkflowError):
        freeze_canvas_credentials(
            record(), {"apiKey": "private-main-key", "headers": headers}, cipher()
        )


def test_host_common_validation_retains_only_exact_relative_base_exception():
    value = CanvasModelChannelInput.model_construct(
        source_key="host-123",
        name="Host",
        base_url="/api/v1/canvas-runtime/ai/models/123",
        reference_asset_origin=None,
        api_key=None,
        secret_key=None,
        headers=[],
        models=[],
        model_profiles=[],
    )
    assert value.validate_channel_fields() is value
    value.reference_asset_origin = value.base_url
    with pytest.raises(ValueError):
        value.validate_channel_fields()


def test_forged_typed_payload_is_revalidated_before_transport():
    with pytest.raises(ValidationError):
        CanvasCredentials(
            apiKey="private-main-key", headers=[{"name": "X-Safe", "value": "bad\nvalue"}]
        )
