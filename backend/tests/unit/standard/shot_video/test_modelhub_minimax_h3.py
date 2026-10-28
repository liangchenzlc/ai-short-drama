"""公开合同的纯单测；不连接供应商，不启动 HTTP 服务或浏览器。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from short_drama.ai.adapters.video.config import VIDEO_ADAPTERS
from short_drama.ai.adapters.video.input_capabilities import video_input_capabilities
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas.generation.tasks.task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas.generation.generation_inputs import generation_payload
from short_drama.service.canvas.generation.generation_parameters import frozen_canvas_parameters

PACKAGE = Path(__file__).resolve().parent
ADAPTER = "modelhub_video.v1"


@pytest.fixture(scope="module")
def adapters():
    override = os.environ.get("MODELHUB_ADAPTER_UNDER_TEST")
    if override is None:
        import short_drama.ai.adapters as product_adapters

        return product_adapters
    target = Path(override).resolve()
    assert target in {PACKAGE / "adapters.before.py", PACKAGE / "adapters.py"}
    spec = importlib.util.spec_from_file_location("short_drama.ai.modelhub_h3_candidate", target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == target
    return module


def snapshot(model="minimax-h3"):
    return {"service_type": "video", "model_key": model, "base_url": "https://api.modelhub.cc"}


def request(parameters=None, inputs=None):
    return {
        "input": {"prompt": "白色桌面上的红色礼盒缓慢转动。", **(inputs or {})},
        "parameters": {
            "duration_ms": 5000,
            "resolution": "480p",
            "aspect": "16:9",
            **(parameters or {}),
        },
    }


@pytest.mark.parametrize("seconds", [5, 6, 10, 15])
def test_minimax_text_submission_uses_public_contract(adapters, seconds):
    url, headers, body = adapters.build_submission(
        snapshot(), request({"duration_ms": seconds * 1000}), ADAPTER
    )
    assert url == "https://api.modelhub.cc/v1/videos/generations" and headers == {}
    assert body == {
        "model": "minimax-h3",
        "prompt": "白色桌面上的红色礼盒缓慢转动。",
        "duration": seconds,
        "resolution": "480p",
        "ratio": "16:9",
        "functionMode": "omni_reference",
    }


def test_minimax_defaults_use_supported_resolution_and_ratio(adapters):
    _, _, body = adapters.build_submission(snapshot(), {"input": {"prompt": "move"}}, ADAPTER)
    assert (body["duration"], body["resolution"], body["ratio"]) == (5, "480p", "9:16")


@pytest.mark.parametrize("count", [1, 2])
def test_minimax_frames_keep_order_without_unsupported_auto_ratio(adapters, count):
    frames = {
        f"{name}_frame_url": f"https://example.org/{name}.png" for name in ("first", "last")[:count]
    }
    _, _, body = adapters.build_submission(
        snapshot(), {"input": {"prompt": "move", **frames}}, ADAPTER
    )
    assert body["ratio"] == "9:16" and body["functionMode"] == "first_last_frames"
    assert [body[f"image_file_{index}"] for index in range(1, count + 1)] == list(frames.values())


def test_minimax_reference_images_keep_omni_mode_and_order(adapters):
    urls = ["https://example.org/red.png", "https://example.org/blue.png"]
    _, _, body = adapters.build_submission(
        snapshot(), request(inputs={"reference_urls": urls}), ADAPTER
    )
    assert body["functionMode"] == "omni_reference"
    assert [body["image_file_1"], body["image_file_2"]] == urls
    assert "first_frame_url" not in body


@pytest.mark.parametrize(
    "parameters",
    [
        {"duration_ms": 4000},
        {"duration_ms": 16000},
        {"duration_ms": 5500},
        {"duration_ms": True},
        {"resolution": "720p"},
        {"resolution": "1080p"},
        {"resolution": "768p"},
        {"aspect": "auto"},
        {"aspect": "3:2"},
        {"generate_audio": True},
        {"generate_audio": False},
    ],
)
def test_minimax_invalid_or_unimplemented_parameters_are_rejected(adapters, parameters):
    with pytest.raises(GenerationError, match="unsupported_parameters"):
        adapters.build_submission(snapshot(), request(parameters), ADAPTER)


@pytest.mark.parametrize(
    "inputs",
    [
        {"audio_reference_urls": ["https://example.org/audio.mp3"]},
        {"video_reference_urls": ["https://example.org/video.mp4"]},
        {"last_frame_url": "https://example.org/last.png"},
        {"reference_media_ids": ["17"]},
        {
            "reference_urls": ["https://example.org/image.png"],
            "first_frame_url": "https://example.org/first.png",
        },
    ],
)
def test_minimax_reference_scope_stays_closed(adapters, inputs):
    with pytest.raises(GenerationError):
        adapters.build_submission(snapshot(), request(inputs=inputs), ADAPTER)


def test_minimax_capabilities_are_limited_to_implemented_images(adapters):
    assert adapters.select_adapter(snapshot()) == ADAPTER
    caps = adapters.capabilities(snapshot())
    assert caps["reference_images"] is True and caps["last_frame"] is True
    assert caps["video_input"]["duration_seconds"] == list(range(5, 16))
    assert caps["video_input"]["resolutions"] == ["480p"]
    assert caps["video_input"]["first_frame"] is True
    assert caps["video_input"]["audio_references"] is False
    assert caps["video_input"]["generate_audio"] is False
    assert caps["video_input"]["max_audio_references"] == 0
    assert not adapters.capabilities({**snapshot(), "base_url": "https://api.modelhub.cc.example"})[
        "known"
    ]


@pytest.mark.parametrize("model", ["unknown", "minimax-h3-other"])
def test_unknown_model_stays_rejected(adapters, model):
    with pytest.raises(GenerationError, match="unsupported_parameters"):
        adapters.build_submission(snapshot(model), request(), ADAPTER)


def test_seedance_capabilities_remain_exact(adapters):
    assert video_input_capabilities(snapshot("seedance-2.0-mini"), ADAPTER) == {
        "first_frame": True,
        "reference_images": True,
        "duration_seconds": list(range(4, 16)),
        "resolutions": ["720p", "480p"],
        "audio_references": True,
        "generate_audio": True,
        "max_audio_references": 2,
        "audio_min_ms": 3000,
        "audio_max_ms": 7500,
        "audio_evidence": "documented_channel_dependent",
        "voice_fidelity": "unverified",
    }


@pytest.mark.parametrize("seconds", [4, 5, 15])
@pytest.mark.parametrize("resolution", ["480p", "720p"])
def test_seedance_previous_duration_and_resolution_are_unchanged(adapters, seconds, resolution):
    _, _, body = adapters.build_submission(
        snapshot("seedance-2.0-mini"),
        request({"duration_ms": seconds * 1000, "resolution": resolution}),
        ADAPTER,
    )
    assert body["duration"] == seconds and body["resolution"] == resolution
    assert body["functionMode"] == "omni_reference"


def test_seedance_frame_default_and_audio_reference_are_unchanged(adapters):
    _, _, frame = adapters.build_submission(
        snapshot("seedance-2.0-mini"),
        {"input": {"prompt": "move", "first_frame_url": "https://example.org/first.png"}},
        ADAPTER,
    )
    assert (frame["ratio"], frame["resolution"]) == ("auto", "720p")
    _, _, audio = adapters.build_submission(
        snapshot("seedance-2.0-mini"),
        request(
            {"generate_audio": True},
            {
                "reference_urls": ["https://example.org/first.png"],
                "audio_reference_urls": ["https://example.org/a.mp3"],
            },
        ),
        ADAPTER,
    )
    assert audio["generate_audio"] is True and audio["audio_file_1"].endswith("/a.mp3")


@pytest.mark.parametrize(
    "status", ["submitted", "not_start", "processing", "pending", "in_queue", "queued"]
)
def test_modelhub_pending_states_preserve_original_task(adapters, status):
    result = adapters.parse_result(
        {"task_id": "original-task", "status": status},
        ADAPTER,
        submitted=False,
        task_id="original-task",
    )
    assert result.status == "submitted" and result.provider_task_id == "original-task"


@pytest.mark.parametrize("status", ["success", "completed"])
@pytest.mark.parametrize("nested", [False, True])
def test_modelhub_completed_response_keeps_documented_url(adapters, status, nested):
    field = (
        {"result": {"url": "https://example.org/output.mp4"}}
        if nested
        else {"url": "https://example.org/output.mp4"}
    )
    result = adapters.parse_result(
        {"task_id": "original-task", "status": status, **field},
        ADAPTER,
        submitted=False,
        task_id="original-task",
    )
    assert result.status == "succeeded"
    assert result.outputs == [{"url": "https://example.org/output.mp4", "media_type": "video"}]


@pytest.mark.parametrize("status", ["failed", "cancelled", "blocked"])
def test_modelhub_failed_states_do_not_become_success(adapters, status):
    result = adapters.parse_result(
        {"status": status}, ADAPTER, submitted=False, task_id="original-task"
    )
    assert result.status == "failed" and result.outputs == []


@pytest.mark.parametrize("submitted", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        {"status": "unrecognized", "task_id": "original-task"},
        {"status": "completed", "result": {}},
        {"status": "completed", "result": {"url": 7}},
        {"status": "submitted"},
    ],
)
def test_modelhub_ambiguous_response_preserves_unknown_and_retry_rules(adapters, submitted, body):
    with pytest.raises(GenerationError) as error:
        adapters.parse_result(body, ADAPTER, submitted=submitted)
    assert error.value.code == "invalid_response"
    assert error.value.accepted_unknown is submitted
    assert error.value.retryable is not submitted


def test_modelhub_poll_uses_original_task_endpoint(adapters):
    assert adapters.poll_endpoint(snapshot(), "original-task", ADAPTER) == (
        "https://api.modelhub.cc/v1/videos/tasks/original-task"
    )


def canvas_request(operation="text_to_video", *, references=None, capability_options=None):
    return CanvasRuntimeTaskCreate.model_validate(
        {
            "projectId": "original-canvas",
            "type": "canvas_video",
            "operation": operation,
            "prompt": "白色桌面上的红色礼盒缓慢转动。",
            "logicalModelId": "17",
            "input": {
                "mode": "video",
                "prompt": "白色桌面上的红色礼盒缓慢转动。",
                "config": {"size": "16:9", "videoSeconds": "5", "vquality": "480p"},
                "referenceImages": references or [],
                "capabilityOptions": capability_options or {},
                "metadata": {
                    "nodeId": "target",
                    "sourceNodeId": "source",
                    "clientOperationId": "fallback-operation",
                },
            },
        }
    )


def test_minimax_uses_existing_canvas_generic_video_mapping(adapters):
    value = canvas_request()
    payload = generation_payload(value, 2, ADAPTER)
    payload["source"] = {"scene": "canvas_node"}
    payload["canvas_parameters"] = frozen_canvas_parameters(value, ADAPTER)
    _, _, body = adapters.build_submission(snapshot(), payload, ADAPTER)
    assert body["duration"] == 5 and body["resolution"] == "480p"
    assert body["ratio"] == "16:9" and ADAPTER not in VIDEO_ADAPTERS


def test_minimax_generic_canvas_image_uses_stable_saved_reference(adapters):
    value = canvas_request(
        "image_to_video",
        references=[{"id": "row-image", "storageKey": "resource:23", "type": "png"}],
    )
    payload = generation_payload(value, 2, ADAPTER)
    assert payload["input"]["first_frame_media_id"] == "23"
    payload["source"] = {"scene": "canvas_node"}
    payload["canvas_parameters"] = frozen_canvas_parameters(value, ADAPTER)
    admitted = adapters.validate_request(snapshot(), payload, ADAPTER)
    assert admitted["adapter"] == ADAPTER
    assert admitted["resolved_parameters"]["functionMode"] == "first_last_frames"
    payload["input"]["first_frame_url"] = "https://example.org/row-image.png"
    _, _, body = adapters.build_submission(snapshot(), payload, ADAPTER)
    assert body["image_file_1"] == "https://example.org/row-image.png"
    assert (body["duration"], body["resolution"], body["ratio"]) == (5, "480p", "16:9")


def test_generic_canvas_capability_options_remain_rejected():
    with pytest.raises(WorkflowError, match="provider/capabilityOptions"):
        generation_payload(canvas_request(capability_options={"videoSeconds": 5}), 2, ADAPTER)
