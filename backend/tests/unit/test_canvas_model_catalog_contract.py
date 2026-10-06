import pytest
from pydantic import ValidationError

from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest


def channel(**changes):
    return {
        "id": "my-channel",
        "name": "我的渠道",
        "baseUrl": "https://provider.example/v1",
        "apiKey": "test-only-key",
        "apiFormat": "openai",
        "models": ["gpt-image-1"],
        "modelProfiles": [
            {"model": "gpt-image-1", "capability": "image", "protocol": "openai-image"}
        ],
        **changes,
    }


def payload(*channels):
    return CanvasWorkspacePreferencesRequest(
        expected_row_version="0", preferences={}, channels=list(channels)
    )


def test_catalog_credentials_are_secret_values_and_ids_remain_strings():
    value = payload(channel())
    assert value.channels[0].api_key.get_secret_value() == "test-only-key"
    assert "test-only-key" not in repr(value)
    assert value.channels[0].source_key == "my-channel"


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "host-123"},
        {"models": ["gpt-image-1", "gpt-image-1"]},
        {"modelProfiles": [{"model": "other", "capability": "image"}]},
        {"modelProfiles": [{"model": "gpt-image-1", "capability": "bogus"}]},
        {"baseUrl": "https://key:secret@provider.example/v1"},
        {"headers": [{"name": "Cookie", "value": "secret"}]},
        {"headers": [{"name": "X-Test", "value": "first\r\nX-Injected: yes"}]},
    ],
)
def test_catalog_rejects_invalid_or_unsafe_channel_data(changes):
    with pytest.raises(ValidationError):
        payload(channel(**changes))


def test_catalog_rejects_duplicate_channels_and_nested_plaintext_credentials():
    with pytest.raises(ValidationError):
        payload(channel(), channel())
    with pytest.raises(ValidationError):
        payload(
            channel(
                modelProfiles=[
                    {
                        "model": "gpt-image-1",
                        "capability": "image",
                        "defaultOptions": {"apiKey": "secret"},
                    }
                ]
            )
        )


def test_catalog_can_preserve_unknown_plugin_protocol_without_claiming_execution_support():
    value = payload(
        channel(
            modelProfiles=[
                {
                    "model": "gpt-image-1",
                    "capability": "image",
                    "protocol": "plugin/custom-image",
                    "capabilityConfig": {"count": {"max": 15}},
                }
            ]
        )
    )
    assert value.channels[0].model_profiles[0].protocol == "plugin/custom-image"


@pytest.mark.parametrize("field", ["protocol", "interfaceType"])
def test_catalog_protocol_identifier_matches_database_provider_length(field):
    changes = (
        {"modelProfiles": [{"model": "gpt-image-1", "capability": "image", "protocol": "p" * 121}]}
        if field == "protocol"
        else {"interfaceType": "p" * 121}
    )
    with pytest.raises(ValidationError):
        payload(channel(**changes))
    changes = (
        {"modelProfiles": [{"model": "gpt-image-1", "capability": "image", "protocol": "p" * 120}]}
        if field == "protocol"
        else {"interfaceType": "p" * 120}
    )
    assert payload(channel(**changes)).channels


@pytest.mark.parametrize("field", ["apiKey", "secretKey", "header"])
def test_catalog_secret_limit_counts_real_utf8_bytes_not_masked_json(field):
    secret = "中" * 5462
    changes = (
        {"headers": [{"name": "X-Private", "value": secret}]}
        if field == "header"
        else {field: secret}
    )
    with pytest.raises(ValidationError):
        payload(channel(**changes))
    accepted = "x" * (4096 if field == "header" else 16384)
    changes = (
        {"headers": [{"name": "X-Private", "value": accepted}]}
        if field == "header"
        else {field: accepted}
    )
    value = payload(channel(**changes))
    assert value.channels and accepted not in repr(value)


def test_catalog_aggregate_budget_counts_secret_bytes_before_encryption():
    channels = [
        channel(id=f"channel-{index}", apiKey="x" * 16384, secretKey="s" * 16384)
        for index in range(65)
    ]
    with pytest.raises(ValidationError):
        payload(*channels)
    assert payload(*channels[:32]).channels


@pytest.mark.parametrize(
    "url",
    [
        "https://provider.example:invalid/v1",
        "https://provider.example:65536/v1",
        "https://provider.example:0/v1",
        "https://provider.example:/v1",
        "https://@provider.example/v1",
        "https://:@provider.example/v1",
        " https://provider.example/v1",
        "https://provider.example/a b",
        "https://provider.example/\tpath",
        "https://provider.example/\\path",
        "https://provider.example?",
        "https://provider.example#",
        "   ",
    ],
)
@pytest.mark.parametrize("field", ["baseUrl", "referenceAssetOrigin"])
def test_catalog_url_rejects_invalid_port_whitespace_and_empty_credentials(field, url):
    with pytest.raises(ValidationError):
        payload(channel(**{field: url}))


def test_catalog_retains_empty_base_draft_and_valid_explicit_ipv6_port():
    assert payload(channel(baseUrl="")).channels
    assert payload(channel(baseUrl="http://[::1]:8080/v1")).channels
