from copy import deepcopy

import pytest
from pydantic import ValidationError

from short_drama.core.config import Settings
from short_drama.schemas.canvas_model_catalog import CanvasModelChannelInput
from short_drama.service.canvas_beefapi_catalog import catalog_profile, merge_catalog


@pytest.mark.parametrize(
    "identifier,kind,endpoints,expected",
    [
        ("gpt-6-astra", "text", ["openai"], ("text", "chat-completion")),
        ("gpt", "text", ["responses", "openai"], ("text", "openai-response")),
        ("claude", "text", ["messages"], ("text", "claude-api")),
        ("gemini", "text", ["gemini"], ("text", "gemini-generate-content")),
        ("image", "", ["images.generations"], ("image", "openai-image")),
        ("video", "video", [], ("video", "openai-videos")),
        ("seedance-2.0-fast", "text", ["openai"], ("video", "newapi")),
        ("seedance-2.5-preview", "", [], ("video", "newapi")),
        ("wan3.0-video", "", [], ("video", "newapi-channel-2")),
        ("wan3.0-video-extra", "", [], ("", "")),
        ("minimax_tts", "", ["openai"], ("audio", "openai-audio")),
        ("minimax-music", "", ["openai"], ("audio", "openai-audio")),
        ("whisper-asr", "audio", ["openai"], ("", "")),
        ("transcriber", "text", ["audio.transcriptions"], ("", "")),
        ("whisper", "text", ["openai"], ("text", "chat-completion")),
        ("unknown", "", [], ("", "")),
    ],
)
def test_fixed_source_capability_mapping(identifier, kind, endpoints, expected):
    value = catalog_profile(
        {"id": identifier, "modelType": kind, "supportedEndpointTypes": endpoints}
    )
    assert (value["capability"], value["protocol"]) == expected


def test_same_account_merge_preserves_local_fields_and_replaces_fresh_capability():
    channel = {
        "models": ["old", "gpt"],
        "modelProfiles": [
            {"model": "old", "capability": "image", "protocol": "openai-image"},
            {"model": "gpt", "capability": "image", "description": "local", "displayName": "old"},
        ],
    }
    original = deepcopy(channel)
    incoming = [
        {"id": "gpt", "modelType": "text", "displayName": "Fresh"},
        {"id": "next", "modelType": "audio"},
    ]
    merged = merge_catalog(channel, incoming, replace=False)
    assert channel == original
    assert merged["models"] == ["old", "gpt", "next"]
    assert merged["modelProfiles"][1]["description"] == "local"
    assert merged["modelProfiles"][1]["displayName"] == "Fresh"
    assert merged["modelProfiles"][1]["capability"] == "text"
    replaced = merge_catalog(channel, incoming, replace=True)
    assert replaced["models"] == ["gpt", "next"]
    assert "description" not in replaced["modelProfiles"][0]


def managed(**changes):
    return {
        "id": "beefapi",
        "name": "BeefAPI",
        "baseUrl": "https://enterprise.beefapi.com",
        "pinned": True,
        "scope": "user",
        "credentialRef": "beefapi-enterprise",
        "models": ["whisper"],
        "modelProfiles": [{"model": "whisper", "capability": "", "protocol": ""}],
        **changes,
    }


def test_managed_projection_permits_non_generation_model_without_secret():
    value = CanvasModelChannelInput.model_validate(managed())
    assert value.model_profiles[0].capability == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"apiKey": "forged-key"},
        {"secretKey": "forged-secret"},
        {"clearCredentials": ["apiKey"]},
        {"pinned": False},
        {"scope": "system"},
        {"credentialRef": "host:other"},
        {"id": "fake", "pinned": False},
    ],
)
def test_managed_projection_rejects_impersonation_and_browser_secrets(changes):
    with pytest.raises(ValidationError):
        CanvasModelChannelInput.model_validate(managed(**changes))


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "http://127.0.0.1:8123/path", "http://127.0.0.1:8123?key=value"],
)
def test_test_origin_cannot_change_to_arbitrary_supplier(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, canvas_beefapi_test_origin=origin)


def test_test_origin_defaults_to_production_and_only_explicit_loopback_is_allowed():
    assert Settings(_env_file=None).canvas_beefapi_test_origin == ""
    assert (
        Settings(
            _env_file=None, canvas_beefapi_test_origin="http://127.0.0.1:8123"
        ).canvas_beefapi_test_origin
        == "http://127.0.0.1:8123"
    )
