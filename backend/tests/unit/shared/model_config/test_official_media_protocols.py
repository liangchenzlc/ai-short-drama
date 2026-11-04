"""Official media HTTP recipes, using published payloads without paid requests."""

import pytest

from short_drama.ai.adapters.results import parse_result, poll_endpoint
from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.types import GenerationError
from tests.unit.shared.generation.test_generation_adapters import gateway as gateway
from tests.unit.shared.generation.test_generation_adapters import provider as provider
from tests.unit.shared.generation.test_generation_adapters import reference_image


@pytest.mark.parametrize(
    "model,adapter,response,payload_key",
    [
        ("MiniMax-H3", "minimax_video.v2", {"task_id": "job-1"}, "content"),
        (
            "kling-4.0",
            "kling_video.v4",
            {"code": 0, "data": {"id": "job-1", "status": "submitted"}},
            "contents",
        ),
    ],
)
def test_native_video_uploads_authorized_images_without_private_urls_or_metadata_bytes(
    gateway, provider, model, adapter, response, payload_key
):
    import base64
    import json

    base, state, calls = provider
    state["body"] = response
    pixels = reference_image()
    result = gateway.submit(
        {"base_url": base, "service_type": "video", "model_key": model},
        {
            "input": {
                "prompt": "A room",
                "first_frame_media_id": "17",
                "first_frame_url": "http://private.example/resource/17",
            }
        },
        "test-only-key",
        adapter,
        reference_loader=lambda *_: pixels,
    )
    assert result.status == "submitted" and len(calls) == 1
    frame = calls[0][3][payload_key][1]
    value = frame["image_url"]["url"] if adapter == "minimax_video.v2" else frame["url"]
    encoded = base64.b64encode(pixels).decode()
    assert value == (
        "data:image/png;base64," + encoded if adapter == "minimax_video.v2" else encoded
    )
    assert "private.example" not in json.dumps(calls[0][3])
    assert encoded not in json.dumps(result.resolved_parameters)


def test_official_minimax_catalog_is_available_without_a_list_models_endpoint():
    from short_drama.ai.adapters.model_protocols import interface_options
    from short_drama.core.config import Settings
    from short_drama.schemas.ai_config.discovery.catalog import ModelDiscoveryRequest
    from short_drama.service.ai_config.discovery.catalog import ModelDiscoveryService

    options = interface_options("video")
    assert {item["id"] for item in options["models"]} >= {"MiniMax-H3", "MiniMax-H3-Lite"}
    result = ModelDiscoveryService(Settings(_env_file=None), None).discover(
        ModelDiscoveryRequest(
            base_url="https://api.minimax.io", service_type="video", interface_type="minimax-video"
        )
    )
    assert result.source == "builtin" and result.status == "available"
    assert "MiniMax-H3" in {item.id for item in result.items}


def test_google_image_discovery_uses_the_selected_protocol_for_custom_prefixes():
    from short_drama.service.ai_config.discovery.catalog import model_paths

    assert model_paths("https://proxy.example/google", api_format="gemini") == [
        "/google/v1beta/models"
    ]


def test_dashscope_text_and_media_presets_use_their_separate_official_endpoints():
    from short_drama.ai.adapters.model_protocols import interface_options

    text = next(
        item for item in interface_options("text")["providers"] if item["id"] == "dashscope"
    )
    image = next(
        item for item in interface_options("image")["providers"] if item["id"] == "dashscope"
    )
    assert text["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert image["base_url"] == "https://dashscope.aliyuncs.com/api/v1"


def test_native_video_models_expose_trusted_controls_without_user_runtime_json():
    from types import SimpleNamespace

    from short_drama.service.ai_config.management.runtime import (
        canvas_model_default_capability_config,
    )

    config = SimpleNamespace(
        service_type="video",
        model_key="MiniMax-H3",
        base_url="https://api.minimax.io",
        runtime_profile={"protocol": "minimax-video", "api_format": "openai"},
    )
    capability = canvas_model_default_capability_config(config)
    assert capability["video"]["duration"]["values"] == list(range(4, 16))
    assert capability["video"]["resolutions"] == ["768p", "2K"]
    assert capability["video"]["references"]["maxImages"] == 9


def test_unknown_discovery_protocol_is_rejected_before_network_io():
    from short_drama.core.config import Settings
    from short_drama.schemas.ai_config.discovery.catalog import ModelDiscoveryRequest
    from short_drama.service.ai_config.discovery.catalog import (
        ModelDiscoveryError,
        ModelDiscoveryService,
    )

    with pytest.raises(ModelDiscoveryError) as caught:
        ModelDiscoveryService(Settings(_env_file=None), None).discover(
            ModelDiscoveryRequest(base_url="https://unsupported.invalid", interface_type="unknown")
        )
    assert caught.value.code == "model_discovery_unsupported_protocol"


def test_wan3_cannot_be_sent_through_the_previous_wan_protocol():
    with pytest.raises(GenerationError, match="unsupported_protocol"):
        build_submission(
            {
                "base_url": "https://dashscope.aliyuncs.com/api/v1",
                "model_key": "wan3.0-t2v",
                "service_type": "video",
            },
            {"input": {"prompt": "A room"}},
            "dashscope_video.v1",
        )


def test_kling4_uses_the_official_versioned_endpoint_and_frame_roles():
    from short_drama.ai.adapters.catalog import select_adapter

    saved = {
        "base_url": "https://api-singapore.klingai.com",
        "model_key": "kling-4.0",
        "service_type": "video",
    }
    adapter = select_adapter(saved)
    assert adapter == "kling_video.v4"
    url, headers, body = build_submission(
        saved,
        {
            "input": {
                "prompt": "A slow pan",
                "first_frame_url": "https://images.example/first.png",
                "last_frame_url": "https://images.example/last.png",
            },
            "parameters": {"duration_ms": 5000, "resolution": "720p"},
        },
        adapter,
    )
    assert url == "https://api-singapore.klingai.com/image-to-video/kling-4.0"
    assert headers == {}
    assert body["contents"] == [
        {"type": "prompt", "text": "A slow pan"},
        {"type": "first_frame", "url": "https://images.example/first.png"},
        {"type": "last_frame", "url": "https://images.example/last.png"},
    ]
    assert body["settings"] == {"resolution": "720p", "duration": 5}


def test_kling4_queries_the_same_task_and_reads_its_video_output():
    saved = {"base_url": "https://api-beijing.klingai.com"}
    queued = parse_result(
        {"code": 0, "data": {"id": "task-4", "status": "submitted"}},
        "kling_video.v4",
        submitted=True,
    )
    assert queued.provider_task_id == "task-4" and queued.status == "submitted"
    assert (
        poll_endpoint(saved, "task-4", "kling_video.v4")
        == "https://api-beijing.klingai.com/tasks?task_ids=task-4"
    )
    done = parse_result(
        {
            "code": 0,
            "data": {
                "result": [
                    {
                        "id": "task-4",
                        "status": "succeeded",
                        "outputs": [{"type": "video", "url": "https://video.example/result.mp4"}],
                    }
                ]
            },
        },
        "kling_video.v4",
        submitted=False,
        task_id="task-4",
    )
    assert done.outputs == [{"url": "https://video.example/result.mp4", "media_type": "video"}]


def test_minimax_h3_uses_v2_and_preserves_first_last_frame_roles():
    saved = {
        "base_url": "https://api.minimax.io",
        "model_key": "MiniMax-H3",
        "service_type": "video",
    }
    url, headers, body = build_submission(
        saved,
        {
            "input": {
                "prompt": "Move the camera",
                "first_frame_url": "https://images.example/first.png",
                "last_frame_url": "https://images.example/last.png",
            },
            "parameters": {"duration_ms": 5000, "resolution": "2K"},
        },
        "minimax_video.v2",
    )
    assert url == "https://api.minimax.io/v2/video_generation"
    assert body["ratio"] == "adaptive"
    assert body["content"][1]["role"] == "first_frame"
    assert body["content"][2]["role"] == "last_frame"
    assert headers == {}


def test_minimax_h3_queries_the_existing_task_and_parses_its_video():
    saved = {"base_url": "https://api.minimax.io"}
    queued = parse_result({"task_id": "task-1"}, "minimax_video.v2", submitted=True)
    assert queued.status == "submitted" and queued.provider_task_id == "task-1"
    assert (
        poll_endpoint(saved, "task-1", "minimax_video.v2")
        == "https://api.minimax.io/v2/query/video_generation/task-1"
    )
    done = parse_result(
        {
            "task": {
                "id": "task-1",
                "status": "succeeded",
                "content": {"url": "https://videos.example/result.mp4"},
                "usage": {"total_seconds": 5},
            }
        },
        "minimax_video.v2",
        submitted=False,
        task_id="task-1",
    )
    assert done.outputs == [{"url": "https://videos.example/result.mp4", "media_type": "video"}]
    assert done.usage["total_seconds"] == 5


def test_minimax_fast_variant_does_not_accept_2k():
    with pytest.raises(GenerationError, match="unsupported_parameters"):
        build_submission(
            {
                "base_url": "https://api.minimax.io",
                "model_key": "MiniMax-H3-Lite",
                "service_type": "video",
            },
            {"input": {"prompt": "A room"}, "parameters": {"resolution": "2K"}},
            "minimax_video.v2",
        )
