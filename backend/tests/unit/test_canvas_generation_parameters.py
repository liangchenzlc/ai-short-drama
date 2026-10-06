"""画布专用参数的供应商请求合同；不调用真实模型。"""

from copy import deepcopy

import pytest

from short_drama.ai import GenerationError
from short_drama.ai.adapters import build_submission
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas_generation_inputs import generation_payload


def request(kind, config=None, *, operation=None, references=None, metadata=None):
    return CanvasRuntimeTaskCreate.model_validate(
        {
            "projectId": "original-canvas",
            "type": f"canvas_{kind}",
            "operation": operation or ("text_to_video" if kind == "video" else kind),
            "prompt": "原版提示词",
            "logicalModelId": "17",
            "input": {
                "mode": kind,
                "prompt": "原版提示词",
                "config": config or {},
                "referenceImages": references or [],
                "metadata": {
                    "nodeId": "target",
                    "sourceNodeId": "source",
                    "clientOperationId": "original-operation",
                    **(metadata or {}),
                },
            },
        }
    )


def frozen_request(value, adapter):
    from short_drama.service.canvas_generation_parameters import frozen_canvas_parameters

    payload = generation_payload(value, 2, adapter)
    payload["source"] = {"scene": "canvas_node"}
    payload["canvas_request"] = value.model_dump(mode="json", by_alias=True)
    payload["canvas_parameters"] = frozen_canvas_parameters(value, adapter)
    return payload


def snapshot(kind, model):
    return {"service_type": kind, "model_key": model, "base_url": "https://example.test/v1"}


def test_canvas_image_quality_is_quality_and_source_pixel_size_reaches_openai():
    payload = frozen_request(
        request("image", {"size": "16:9", "quality": "2k"}), "openai_images.v1"
    )
    _, _, body = build_submission(snapshot("image", "gpt-image-2"), payload, "openai_images.v1")
    assert body["size"] == "1824x1024"
    assert body["quality"] == "medium" and body["output_format"] == "png"
    assert "resolution" not in payload["parameters"]


@pytest.mark.parametrize("size", ["2752x1536", "2880x2880", "1024x1280"])
def test_canvas_openai_image_preserves_source_pixel_presets(size):
    payload = frozen_request(request("image", {"size": size}), "openai_images.v1")
    _, _, body = build_submission(snapshot("image", "gpt-image-2"), payload, "openai_images.v1")
    assert body["size"] == size


def test_canvas_openai_auto_omits_size_and_quality_and_transparent_background_is_explicit():
    payload = frozen_request(
        request("image", {"size": "auto", "quality": "auto", "transparentBackground": "true"}),
        "openai_images.v1",
    )
    _, _, body = build_submission(snapshot("image", "gpt-image-2"), payload, "openai_images.v1")
    assert "size" not in body and "quality" not in body
    assert body["background"] == "transparent" and body["output_format"] == "png"


@pytest.mark.parametrize(
    "size,expected", [("16:9", "2560x1440"), ("auto", "2k"), ("2752x1536", "2752x1536")]
)
def test_canvas_ark_uses_actual_declarative_manifest_without_legacy_pixel_rescaling(size, expected):
    payload = frozen_request(request("image", {"size": size, "quality": "2k"}), "ark_images.v1")
    _, _, body = build_submission(
        snapshot("image", "doubao-seedream-5-0"), payload, "ark_images.v1"
    )
    assert body == {
        "model": "doubao-seedream-5-0",
        "prompt": "原版提示词",
        "size": expected,
        "response_format": "b64_json",
        "watermark": False,
    }


def test_canvas_qwen_image_uses_source_async_endpoint_and_preserves_reference_order():
    payload = frozen_request(
        request(
            "image",
            {"size": "3:4", "quality": "2k"},
            references=[{"storageKey": "resource:12"}, {"storageKey": "resource:13"}],
        ),
        "dashscope_images.v1",
    )
    payload["input"]["reference_urls"] = ["https://example.test/12", "https://example.test/13"]
    settings = snapshot("image", "qwen-image-3.0-pro")
    settings["base_url"] = "https://example.test/compatible-mode/v1"
    url, headers, body = build_submission(settings, payload, "dashscope_images.v1")
    assert url == "https://example.test/api/v1/services/aigc/image-generation/generation"
    assert headers["X-DashScope-Async"] == "enable"
    assert body["input"]["messages"][0]["content"] == [
        {"image": "https://example.test/12"},
        {"image": "https://example.test/13"},
        {"text": "原版提示词"},
    ]
    assert body["parameters"] == {
        "n": 1,
        "size": "960*1280",
        "prompt_extend": True,
        "enable_thinking": False,
        "watermark": False,
    }


def test_canvas_qwen_omits_unregistered_size_as_source_manifest_does():
    payload = frozen_request(request("image", {"size": "2880x2880"}), "dashscope_images.v1")
    _, _, body = build_submission(
        snapshot("image", "qwen-image-3.0-pro"), payload, "dashscope_images.v1"
    )
    assert "size" not in body["parameters"]


@pytest.mark.parametrize(
    "adapter,model,expected",
    [
        ("openai_images.v1", "gpt-image-2", "1024x1024"),
        ("ark_images.v1", "doubao-seedream-5-0", "2048x2048"),
        ("dashscope_images.v1", "qwen-image-3.0-pro", "1024*1024"),
    ],
)
def test_canvas_blank_size_applies_source_default_before_manifest_mapping(adapter, model, expected):
    for config in ({}, {"size": " "}):
        payload = frozen_request(request("image", config), adapter)
        _, _, body = build_submission(snapshot("image", model), payload, adapter)
        assert (body["parameters"] if adapter == "dashscope_images.v1" else body)[
            "size"
        ] == expected


@pytest.mark.parametrize("adapter", ["openai_images.v1", "ark_images.v1", "dashscope_images.v1"])
def test_invalid_canvas_image_size_returns_workflow_error_before_admission(adapter):
    with pytest.raises(WorkflowError) as error:
        generation_payload(request("image", {"size": "not-a-ratio"}), 2, adapter)
    assert error.value.status_code == 422 and "not-a-ratio" not in error.value.message


def test_standard_image_wire_body_remains_unchanged():
    value = {"input": {"prompt": "标准图片"}, "parameters": {"aspect": "16:9", "resolution": "2K"}}
    _, _, body = build_submission(snapshot("image", "gpt-image-2"), value, "openai_images.v1")
    assert body == {"model": "gpt-image-2", "prompt": "标准图片", "n": 1, "size": "2560x1440"}


@pytest.mark.parametrize(
    "option,value",
    [("size", True), ("size", "0x1024"), ("quality", False), ("quality", "private-quality")],
)
def test_canvas_image_options_are_strict_and_do_not_echo_invalid_values(option, value):
    with pytest.raises(WorkflowError) as error:
        generation_payload(request("image", {option: value}), 2, "openai_images.v1")
    assert error.value.status_code == 422 and option in error.value.message
    assert "private-quality" not in error.value.message


@pytest.mark.parametrize("adapter", ["ark_images.v1", "dashscope_images.v1"])
def test_source_channels_without_transparency_reject_explicit_transparent_request(adapter):
    with pytest.raises(WorkflowError, match="transparentBackground"):
        generation_payload(request("image", {"transparentBackground": True}), 2, adapter)


def test_canvas_image_frozen_options_cannot_supply_credentials_or_replace_standard_parameters():
    original = frozen_request(request("image", {"size": "auto"}), "openai_images.v1")
    for extra in ({"apikey": "private-value"}, {"size": True}, {"transparent_background": "true"}):
        payload = deepcopy(original)
        payload["canvas_parameters"].update(extra)
        with pytest.raises(GenerationError) as error:
            build_submission(snapshot("image", "gpt-image-2"), payload, "openai_images.v1")
        assert "private-value" not in str(error.value)
    original["parameters"]["resolution"] = "1K"
    with pytest.raises(GenerationError):
        build_submission(snapshot("image", "gpt-image-2"), original, "openai_images.v1")


@pytest.mark.parametrize("count", ["0", "5", True, "private-count"])
def test_invalid_canvas_count_is_a_request_error_instead_of_validation_500(count):
    with pytest.raises(WorkflowError) as error:
        generation_payload(request("image", {"count": count}), 2, "openai_images.v1")
    assert error.value.status_code == 422 and "private-count" not in error.value.message


def test_frozen_canvas_audio_options_are_not_silently_ignored_by_adapter():
    payload = {
        "source": {"scene": "canvas_node"},
        "input": {"text": "画布配音"},
        "parameters": {"voice": "alloy"},
        "canvas_parameters": {
            "mode": "audio",
            "format": "mp3",
            "speed": 1.5,
            "instructions": "温暖自然的旁白",
        },
    }
    _, _, body = build_submission(snapshot("audio", "gpt-4o-mini-tts"), payload, "openai_speech.v1")
    assert body["response_format"] == "mp3"
    assert body["speed"] == 1.5 and body["instructions"] == "温暖自然的旁白"


def test_canvas_audio_options_reach_speech_body_without_changing_standard_wav():
    value = request(
        "audio",
        {
            "audioVoice": "alloy",
            "audioFormat": "mp3",
            "audioSpeed": "1.5",
            "audioInstructions": "温暖自然的旁白",
            "audioPitch": "0",
            "audioVolume": "1",
        },
    )
    payload = frozen_request(value, "openai_speech.v1")
    _, _, body = build_submission(snapshot("audio", "gpt-4o-mini-tts"), payload, "openai_speech.v1")
    assert body == {
        "model": "gpt-4o-mini-tts",
        "input": "原版提示词",
        "voice": "alloy",
        "response_format": "mp3",
        "speed": 1.5,
        "instructions": "温暖自然的旁白",
    }
    standard = {"input": {"text": "标准配音"}, "parameters": {"voice": "alloy"}}
    _, _, body = build_submission(
        snapshot("audio", "gpt-4o-mini-tts"), standard, "openai_speech.v1"
    )
    assert body["response_format"] == "wav"
    assert "speed" not in body and "instructions" not in body


@pytest.mark.parametrize("audio_format", ["mp3", "wav", "opus", "aac", "flac"])
def test_canvas_audio_format_is_not_replaced_by_wav(audio_format):
    payload = frozen_request(request("audio", {"audioFormat": audio_format}), "openai_speech.v1")
    _, _, body = build_submission(snapshot("audio", "tts-1"), payload, "openai_speech.v1")
    assert body["response_format"] == audio_format and body["speed"] == 1


@pytest.mark.parametrize(
    "option,value",
    [
        ("audioSpeed", True),
        ("audioSpeed", "nan"),
        ("audioSpeed", "0.2"),
        ("audioSpeed", "4.1"),
        ("audioFormat", "unsupported-format"),
        ("audioFormat", False),
        ("audioPitch", "2"),
        ("audioVolume", "0.5"),
        ("audioInstructions", True),
        ("audioInstructions", False),
    ],
)
def test_canvas_audio_invalid_or_unimplemented_options_fail_before_model_request(option, value):
    with pytest.raises(WorkflowError) as error:
        generation_payload(request("audio", {option: value}), 2, "openai_speech.v1")
    assert error.value.status_code == 422 and option in error.value.message


@pytest.mark.parametrize("enabled", [True, False, "true", "false"])
def test_canvas_ark_video_preserves_explicit_boolean_flags(enabled):
    payload = frozen_request(
        request("video", {"videoGenerateAudio": enabled, "videoWatermark": enabled}), "ark_video.v1"
    )
    _, _, body = build_submission(
        snapshot("video", "doubao-seedance-1-5-pro"), payload, "ark_video.v1"
    )
    assert body["generate_audio"] is (enabled in {True, "true"})
    assert body["watermark"] is (enabled in {True, "true"})


@pytest.mark.parametrize("enabled", [True, False])
def test_modelhub_sends_explicit_generate_audio_and_rejects_even_false_watermark(enabled):
    value = request("video", {"videoGenerateAudio": enabled})
    payload = frozen_request(value, "modelhub_video.v1")
    _, _, body = build_submission(
        snapshot("video", "seedance-2.0-mini"), payload, "modelhub_video.v1"
    )
    assert body["generate_audio"] is enabled
    with pytest.raises(WorkflowError, match="videoWatermark"):
        generation_payload(request("video", {"videoWatermark": False}), 2, "modelhub_video.v1")


def test_ark_image_to_video_uses_explicit_source_frame_ids_instead_of_list_order():
    value = request(
        "video",
        operation="image_to_video",
        references=[
            {"id": "end", "storageKey": "resource:13"},
            {"id": "start", "storageKey": "resource:12"},
        ],
        metadata={"videoStartFrameNodeId": "start", "videoEndFrameNodeId": "end"},
    )
    payload = frozen_request(value, "ark_video.v1")
    assert payload["input"] == {
        "prompt": "原版提示词",
        "reference_media_ids": [],
        "first_frame_media_id": "12",
        "last_frame_media_id": "13",
    }
    payload["input"].update(
        first_frame_url="https://example.test/start.png",
        last_frame_url="https://example.test/end.png",
    )
    _, _, body = build_submission(
        snapshot("video", "doubao-seedance-1-5-pro"), payload, "ark_video.v1"
    )
    assert [(item["role"], item["image_url"]["url"]) for item in body["content"][1:]] == [
        ("first_frame", "https://example.test/start.png"),
        ("last_frame", "https://example.test/end.png"),
    ]


def test_modelhub_omni_reference_keeps_order_and_does_not_turn_refs_into_frames():
    value = request(
        "video",
        operation="reference_to_video",
        references=[
            {"id": "a", "storageKey": "resource:12"},
            {"id": "b", "storageKey": "resource:13"},
        ],
        metadata={"videoStartFrameNodeId": "a"},
    )
    payload = frozen_request(value, "modelhub_video.v1")
    assert payload["input"]["reference_media_ids"] == ["12", "13"]
    assert "first_frame_media_id" not in payload["input"]
    payload["input"]["reference_urls"] = ["https://example.test/a", "https://example.test/b"]
    _, _, body = build_submission(
        snapshot("video", "seedance-2.0-mini"), payload, "modelhub_video.v1"
    )
    assert body["functionMode"] == "omni_reference"
    assert body["image_file_1"] == "https://example.test/a"
    assert body["image_file_2"] == "https://example.test/b"


@pytest.mark.parametrize(
    "references,metadata",
    [
        ([{"id": "a", "storageKey": "resource:12"}], {"videoStartFrameNodeId": "missing"}),
        ([{"id": "a", "storageKey": "resource:12"}], {"videoEndFrameNodeId": "a"}),
        (
            [
                {"id": "a", "storageKey": "resource:12"},
                {"id": "extra", "storageKey": "resource:13"},
            ],
            {"videoStartFrameNodeId": "a"},
        ),
    ],
)
def test_missing_or_mixed_frame_roles_do_not_drop_references(references, metadata):
    with pytest.raises(WorkflowError):
        generation_payload(
            request("video", operation="image_to_video", references=references, metadata=metadata),
            2,
            "ark_video.v1",
        )


@pytest.mark.parametrize("scene", [None, "shot_video"])
def test_canvas_options_cannot_be_injected_into_standard_requests(scene):
    payload = frozen_request(request("video", {"videoWatermark": False}), "ark_video.v1")
    if scene is None:
        payload.pop("source")
    else:
        payload["source"] = {"scene": scene}
    with pytest.raises(GenerationError):
        build_submission(snapshot("video", "doubao-seedance-1-5-pro"), payload, "ark_video.v1")


def test_frozen_canvas_parameter_schema_rejects_unknown_keys_and_wrong_mode():
    original = frozen_request(request("audio"), "openai_speech.v1")
    for extra in ({"apikey": "private-value"}, {"mode": "video"}, {"speed": True}):
        payload = deepcopy(original)
        payload["canvas_parameters"].update(extra)
        with pytest.raises(GenerationError) as error:
            build_submission(snapshot("audio", "tts-1"), payload, "openai_speech.v1")
        assert "private-value" not in str(error.value)
