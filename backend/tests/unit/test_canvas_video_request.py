"""视频准入冻结源默认值与能力选项，原幂等正文保持不变。"""

from copy import deepcopy

import pytest
from test_canvas_video_admission import request

from short_drama.core.exceptions import GenerationRequestError, WorkflowError
from short_drama.service import canvas_generation_inputs


def normalize(value, capability_cache=None):
    return canvas_generation_inputs.normalize_video_request(value, capability_cache)


def profile():
    return {
        "canvas_channel_key": "custom",
        "canvas_video_capability": {
            "duration": {"selection": "enum", "values": [5, 10], "default": 5},
            "ratios": ["16:9", "9:16"],
            "defaultRatio": "9:16",
            "resolutions": ["720p", "1080P"],
            "defaultResolution": "1080P",
            "generateAudio": {"supported": True, "default": False},
            "watermark": {"supported": True, "default": False},
        },
    }


def test_video_admission_freezes_defaults_and_canonical_options_without_changing_raw_body():
    value = request(count=0)
    value.input.config = {"videoSeconds": " ", "size": "\t", "videoGenerateAudio": " "}
    before = deepcopy(value.model_dump())
    normalized = normalize(value, profile())
    assert normalized.input.config == {
        "videoSeconds": "5",
        "size": "9:16",
        "vquality": "1080P",
        "videoGenerateAudio": "false",
        "videoWatermark": "false",
    }
    assert normalized.input.capability_options == {
        "videoSeconds": 5,
        "size": "9:16",
        "vquality": "1080P",
        "videoGenerateAudio": False,
        "videoWatermark": False,
    }
    assert value.model_dump() == before
    result = canvas_generation_inputs.generation_payload(normalized, 9, "canvas_openai_videos.v1")
    assert result["input"]["prompt"] == value.prompt


def test_video_alias_options_override_config_before_defaults_and_preserve_operation_identity():
    value = request(count=0)
    value.input.config = {"videoSeconds": "5", "size": "9:16", "vquality": "1080P"}
    value.input.capability_options = {
        "duration": 10,
        "aspectRatio": "16:9",
        "resolution": "720",
        "videoGenerateAudio": True,
    }
    normalized = normalize(value, profile())
    assert normalized.input.config["videoSeconds"] == "10"
    assert normalized.input.config["size"] == "16:9"
    assert normalized.input.config["vquality"] == "720p"
    assert normalized.input.config["videoGenerateAudio"] == "true"
    assert normalized.input.metadata == value.input.metadata
    assert normalized.input.capability_options["videoSeconds"] == 10
    assert "duration" not in normalized.input.capability_options


def test_managed_single_resolution_is_frozen_while_custom_keeps_explicit_selection():
    value = request(count=0)
    value.input.config = {"vquality": "1080p"}
    capabilities = profile()
    capabilities["canvas_video_capability"]["resolutions"] = ["720P"]
    capabilities["canvas_video_capability"]["defaultResolution"] = "720P"
    with pytest.raises(GenerationRequestError):
        normalize(value, capabilities)
    capabilities["canvas_channel_key"] = "beefapi"
    assert normalize(value, capabilities).input.config["vquality"] == "720P"


@pytest.mark.parametrize("seconds", ["6", "-1", "private-invalid-duration"])
def test_video_invalid_profile_option_is_rejected_without_echoing_client_content(seconds):
    value = request(count=0)
    value.input.config = {"videoSeconds": seconds}
    with pytest.raises(GenerationRequestError) as caught:
        normalize(value, profile())
    assert caught.value.code == "generation_unsupported_parameters"
    assert "private-invalid-duration" not in caught.value.message


@pytest.mark.parametrize("option", ["privateOption", "headers"])
def test_video_unknown_capability_fields_cannot_replace_transport_config(option):
    value = request(count=0)
    value.input.capability_options = {option: "private-value"}
    with pytest.raises((GenerationRequestError, WorkflowError)) as caught:
        normalize(value, profile())
    assert "private-value" not in caught.value.message
