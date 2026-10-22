import pytest
from pydantic import ValidationError

from short_drama.schemas.canvas_model_test import CanvasModelTestCreate


def request(**changes):
    return {
        "channel": {
            "id": "custom-test",
            "name": "测试服务",
            "baseUrl": "https://provider.example/v1",
            "apiKey": "test-only-model-key",
            "apiFormat": "openai",
            "models": ["gpt-test"],
            "modelProfiles": [
                {"model": "gpt-test", "capability": "text", "protocol": "chat-completion"}
            ],
        },
        "model": "gpt-test",
        "mode": "text",
        "prompt": "Reply with OK.",
        "config": {},
        "textOptions": {"stream": False, "thinking": False},
        "clientOperationId": "operation-model-test",
        **changes,
    }


def test_model_test_accepts_unsaved_draft_and_keeps_keys_secret():
    value = CanvasModelTestCreate.model_validate(request())
    assert value.channel.api_key.get_secret_value() == "test-only-model-key"
    assert "test-only-model-key" not in repr(value)
    assert not hasattr(value, "project_id")


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "image"},
        {"model": "other"},
        {"projectId": "canvas-key"},
        {"textOptions": {"stream": True}},
        {"textOptions": {"thinking": True}},
    ],
)
def test_model_test_rejects_ambiguous_or_out_of_scope_requests(changes):
    with pytest.raises(ValidationError):
        CanvasModelTestCreate.model_validate(request(**changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("baseUrl", "https://provider.example:invalid/v1"),
        ("apiKey", "密" * 6000),
        ("referenceAssetOrigin", "https://user:secret@provider.example/"),
    ],
    ids=["invalid-port", "oversized-secret", "url-credentials"],
)
def test_host_model_test_reuses_shared_secret_and_url_validation(field, value):
    body = request()
    body["channel"].update(id="host-123", scope="system")
    body["channel"][field] = value
    with pytest.raises(ValidationError):
        CanvasModelTestCreate.model_validate(body)
