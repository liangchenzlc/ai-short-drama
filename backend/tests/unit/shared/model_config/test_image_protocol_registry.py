"""Image protocol selection and real request/response recipes share one registry."""

import pytest

from short_drama.ai.adapters.catalog import select_adapter
from short_drama.ai.adapters.results import parse_result
from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.types import GenerationError


def snapshot(protocol, model="image-model", base="https://images.example/v1"):
    return {
        "service_type": "image",
        "model_key": model,
        "base_url": base,
        "runtime_profile": {"version": 1, "api_format": "openai", "protocol": protocol},
    }


def test_existing_aliases_select_one_registered_adapter():
    assert select_adapter(snapshot("qwen-image")) == "dashscope_images.v1"
    assert select_adapter(snapshot("dashscope-qwen-image")) == "dashscope_images.v1"


def test_automatic_image_selection_requires_a_trusted_catalog():
    with pytest.raises(GenerationError, match="unsupported_protocol"):
        select_adapter(snapshot("auto"))
    assert select_adapter(snapshot("auto", base="https://api.x.ai/v1")) == "grok_images.v1"


def test_runninghub_image_protocol_is_retired():
    from pydantic import ValidationError

    from short_drama.schemas.ai_config.management.runtime_profile import ModelRuntimeProfile

    with pytest.raises(GenerationError, match="unsupported_protocol"):
        select_adapter(snapshot("runninghub-workflow-image"))
    with pytest.raises(ValidationError):
        ModelRuntimeProfile(protocol="runninghub-workflow-image")


@pytest.mark.parametrize(
    "protocol,adapter",
    [
        ("openai-image", "openai_images.v1"),
        ("volcengine-ark-image", "ark_images.v1"),
        ("qwen-image", "dashscope_images.v1"),
        ("gemini-image", "gemini_images.v1"),
        ("grok-image", "grok_images.v1"),
    ],
)
def test_image_registry_drives_model_management_and_execution(protocol, adapter):
    from short_drama.service.ai_config.management.runtime import runtime_protocol_adapter

    saved = snapshot(protocol)
    assert (
        runtime_protocol_adapter(saved["runtime_profile"], "", saved["model_key"], "image")
        == adapter
    )
    assert select_adapter(saved) == adapter


def test_grok_edit_keeps_its_json_image_contract():
    saved = snapshot("grok-image", "grok-imagine-image")
    url, headers, body = build_submission(
        saved,
        {
            "input": {
                "prompt": "A quiet room",
                "reference_urls": ["https://images.example/ref.png"],
            },
            "parameters": {"aspect": "16:9", "count": 1},
        },
        select_adapter(saved),
    )
    assert url.endswith("/images/edits")
    assert body["image"] == {"url": "https://images.example/ref.png"}
    assert body["aspect_ratio"] == "16:9"
    assert headers == {}
    result = parse_result(
        {"data": [{"url": "https://images.example/result.png"}]}, "grok_images.v1", submitted=True
    )
    assert result.status == "succeeded" and result.outputs[0]["media_type"] == "image"


def test_gemini_image_returns_inline_pixels_and_uses_generate_content():
    from short_drama.ai.adapters.capabilities import resolved_parameters

    saved = snapshot(
        "gemini-image", "gemini-2.5-flash-image", "https://generativelanguage.googleapis.com"
    )
    saved["runtime_profile"]["api_format"] = "gemini"
    url, _, body = build_submission(saved, {"input": {"prompt": "A room"}}, select_adapter(saved))
    assert url.endswith("/v1beta/models/gemini-2.5-flash-image:generateContent")
    assert body["contents"][0]["parts"] == [{"text": "A room"}]
    assert resolved_parameters(body) == {"generationConfig": body["generationConfig"]}
    result = parse_result(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"inlineData": {"mimeType": "image/png", "data": "aW1hZ2U="}}]
                    }
                }
            ]
        },
        "gemini_images.v1",
        submitted=True,
    )
    assert result.outputs == [{"base64": "aW1hZ2U=", "mime": "image/png", "media_type": "image"}]
