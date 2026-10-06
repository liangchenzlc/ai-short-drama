"""固定源视频选项归一化；不联网、不调用真实供应商。"""

from copy import deepcopy

import pytest
from test_canvas_video_transport import NEWAPI, OPENAI, SEEDANCE, frozen_video, video_request

from short_drama.ai import canvas_video_adapters
from short_drama.ai.adapters import build_submission
from short_drama.ai.types import GenerationError


def profile_snapshot(*, managed=True, profile=None, model="seedance-2.5"):
    snapshot = frozen_video("https://enterprise.beefapi.com", model, managed=managed)
    snapshot["canvas_video_capability"] = profile or {
        "duration": {"selection": "enum", "values": [5, 10], "default": 5},
        "ratios": ["9:16", "16:9"],
        "defaultRatio": "9:16",
        "resolutions": ["720P", "1080P"],
        "defaultResolution": "1080P",
        "generateAudio": {"supported": True, "default": False},
        "watermark": {"supported": False, "default": False},
    }
    return snapshot


def request_config(config):
    payload = video_request()
    payload["canvas_request"]["input"]["config"] = config
    payload["canvas_parameters"] = {"mode": "video"}
    return payload


def normalize(snapshot, payload):
    return canvas_video_adapters.normalize_canvas_video_config(snapshot, payload)


@pytest.mark.parametrize(
    "config", [{}, {"videoSeconds": " ", "size": "\t", "vquality": "", "videoGenerateAudio": None}]
)
def test_missing_options_use_frozen_profile_defaults_without_mutating_raw(config):
    payload = request_config(config)
    original = deepcopy(payload)
    assert normalize(profile_snapshot(), payload) == {
        "videoSeconds": "5",
        "size": "9:16",
        "vquality": "1080P",
        "videoGenerateAudio": "false",
        "videoWatermark": "false",
    }
    assert payload == original


def test_build_directly_uses_profile_defaults_and_audio_default():
    body = build_submission(profile_snapshot(), request_config({}), SEEDANCE)[2]
    assert body == {
        "model": "seedance-2.5",
        "prompt": "源提示词",
        "duration": 5,
        "aspect_ratio": "9:16",
        "resolution": "1080P",
        "generate_audio": False,
    }


def test_explicit_values_override_defaults_and_resolution_keeps_declared_case():
    payload = request_config(
        {"videoSeconds": "10", "size": "16:9", "vquality": "720", "videoGenerateAudio": True}
    )
    config = normalize(profile_snapshot(), payload)
    assert config["videoSeconds"] == "10" and config["size"] == "16:9"
    assert config["vquality"] == "720P" and config["videoGenerateAudio"] is True


def test_disabled_duration_does_not_introduce_a_default():
    snapshot = profile_snapshot()
    snapshot["canvas_video_capability"]["durationSupported"] = False
    assert "videoSeconds" not in normalize(snapshot, request_config({}))


@pytest.mark.parametrize(
    "seconds,allowed",
    [
        ("3", True),
        ("7", True),
        ("11", True),
        ("15", True),
        ("6", False),
        ("2", False),
        ("16", False),
    ],
)
def test_range_duration_respects_source_step(seconds, allowed):
    snapshot = profile_snapshot(
        profile={"duration": {"selection": "range", "min": 3, "max": 15, "step": 4, "default": 7}}
    )
    payload = request_config({"videoSeconds": seconds})
    if allowed:
        assert normalize(snapshot, payload)["videoSeconds"] == seconds
    else:
        with pytest.raises(GenerationError):
            normalize(snapshot, payload)


@pytest.mark.parametrize("adapter", [OPENAI, NEWAPI, SEEDANCE])
def test_minus_one_requires_explicit_enum_support(adapter):
    snapshot = profile_snapshot(
        profile={"duration": {"selection": "enum", "values": [-1, 5, 10], "default": 5}}
    )
    payload = request_config({"videoSeconds": "-1"})
    body = build_submission(snapshot, payload, adapter)[2]
    assert body.get("seconds", str(body.get("duration"))) == "-1"
    snapshot["canvas_video_capability"]["duration"]["values"] = [5, 10]
    with pytest.raises(GenerationError):
        normalize(snapshot, payload)


@pytest.mark.parametrize(
    "ratio,allowed",
    [
        ("16:9", True),
        ("1600x900", True),
        ("1600×900", True),
        ("1600.0x900.0", True),
        ("1.6e3x9e2", True),
        ("1770x1000", True),
        ("1800x1000", False),
        ("4:3", False),
    ],
)
def test_ratio_checks_profile_and_source_pixel_tolerance(ratio, allowed):
    snapshot = profile_snapshot()
    payload = request_config({"videoSeconds": "5", "size": ratio})
    if allowed:
        config = normalize(snapshot, payload)
        assert config["size"] == ratio
        assert build_submission(snapshot, payload, OPENAI)[2]["size"] == ratio
    else:
        with pytest.raises(GenerationError):
            normalize(snapshot, payload)


@pytest.mark.parametrize(
    "resolution,expected",
    [
        ("720", "720P"),
        ("720p", "720P"),
        ("4k", "4K"),
        ("2160p", "4K"),
        ("auto", "auto"),
        ("medium", "medium"),
        ("high", "high"),
        ("default", "default"),
    ],
)
def test_resolution_matching_keeps_source_aliases_and_automatic_values(resolution, expected):
    snapshot = profile_snapshot()
    snapshot["canvas_video_capability"]["resolutions"] = ["720P", "4K"]
    snapshot["canvas_video_capability"]["defaultResolution"] = "720P"
    config = normalize(snapshot, request_config({"videoSeconds": "5", "vquality": resolution}))
    assert config["vquality"] == expected


def test_unsupported_resolution_is_rejected_before_submission():
    with pytest.raises(GenerationError):
        normalize(profile_snapshot(), request_config({"videoSeconds": "5", "vquality": "360p"}))


def test_managed_single_sku_pins_resolution_while_custom_validates_selection():
    managed = profile_snapshot()
    managed["canvas_video_capability"]["resolutions"] = ["1080P"]
    payload = request_config({"videoSeconds": "5", "vquality": "720p"})
    assert normalize(managed, payload)["vquality"] == "1080P"
    custom = {**managed, "canvas_channel_key": ""}
    with pytest.raises(GenerationError):
        normalize(custom, payload)


@pytest.mark.parametrize(
    "automatic,expected",
    [("auto", "720p"), ("default", "defaultp"), ("medium", "720p"), ("high", "720p")],
)
def test_seedance_automatic_resolution_uses_source_wire_fallback(automatic, expected):
    payload = request_config({"videoSeconds": "5", "vquality": automatic})
    assert build_submission(profile_snapshot(), payload, SEEDANCE)[2]["resolution"] == expected


def test_no_profile_preserves_raw_config_and_ordinary_wire_defaults():
    snapshot = frozen_video("https://provider.example", "custom")
    payload = request_config({})
    assert normalize(snapshot, payload) == {}
    body = build_submission(snapshot, payload, OPENAI)[2]
    assert body["seconds"] == "6" and body["size"] == "16:9"


def test_trusted_named_ratio_is_preserved_by_direct_build():
    snapshot = profile_snapshot()
    snapshot["canvas_video_capability"]["ratios"].append("cinematic")
    payload = request_config({"videoSeconds": "5", "size": "cinematic"})
    assert build_submission(snapshot, payload, OPENAI)[2]["size"] == "cinematic"


def test_custom_channel_id_does_not_enable_managed_single_sku_pinning():
    snapshot = profile_snapshot()
    snapshot["canvas_channel_key"] = "custom-private-channel"
    snapshot["canvas_video_capability"]["resolutions"] = ["1080P"]
    with pytest.raises(GenerationError):
        normalize(snapshot, request_config({"videoSeconds": "5", "vquality": "720p"}))


def test_declared_video_options_override_config_with_canonical_aliases():
    payload = request_config(
        {"videoSeconds": "5", "size": "9:16", "vquality": "720p", "videoGenerateAudio": "true"}
    )
    payload["canvas_request"]["input"]["capabilityOptions"] = {
        "duration": 10.0,
        "aspectRatio": "16:9",
        "resolution": "1080p",
        "videoGenerateAudio": False,
    }
    payload["canvas_parameters"]["generate_audio"] = True
    config = normalize(profile_snapshot(), payload)
    assert config["videoSeconds"] == "10" and config["size"] == "16:9"
    assert config["vquality"] == "1080P" and config["videoGenerateAudio"] == "false"
    body = build_submission(profile_snapshot(), payload, SEEDANCE)[2]
    assert body["duration"] == 10 and body["aspect_ratio"] == "16:9"
    assert body["generate_audio"] is False


def test_declared_options_can_supply_missing_config_without_mutating_raw():
    payload = request_config({})
    payload["canvas_request"]["input"]["capabilityOptions"] = {
        "videoSeconds": 10,
        "size": "16:9",
        "vquality": "720p",
    }
    original = deepcopy(payload)
    config = normalize(profile_snapshot(), payload)
    assert (
        config["videoSeconds"] == "10" and config["size"] == "16:9" and config["vquality"] == "720P"
    )
    assert payload == original


@pytest.mark.parametrize(
    "options",
    [
        {"secretKey": "private"},
        {"baseUrl": "https://attacker.example"},
        {"providerOptions": {"secret": "private"}},
        {"duration": 10.5},
        {"duration": [10]},
        {"videoGenerateAudio": "invalid"},
    ],
)
def test_declared_options_reject_transport_fields_and_invalid_scalars(options):
    payload = request_config({"videoSeconds": "5"})
    payload["canvas_request"]["input"]["capabilityOptions"] = options
    with pytest.raises(GenerationError):
        normalize(profile_snapshot(), payload)


def edit_request(operation):
    payload = request_config({"videoSeconds": "5", "size": "16:9", "vquality": "720p"})
    payload["canvas_request"]["operation"] = operation
    payload["canvas_request"]["input"]["metadata"]["videoEditOperation"] = operation
    payload["canvas_request"]["input"]["referenceVideos"] = [
        {"url": "https://cdn.example/video.mp4"}
    ]
    return payload


@pytest.mark.parametrize("operation", ["extend", "inpaint", "replace_element", "style_transfer"])
def test_reference_capability_does_not_implicitly_enable_video_edit_operations(operation):
    snapshot = profile_snapshot()
    snapshot["canvas_video_capability"]["operations"] = ["reference_to_video"]
    with pytest.raises(GenerationError, match="unsupported_video_operation"):
        build_submission(snapshot, edit_request(operation), SEEDANCE)


@pytest.mark.parametrize("operation", ["extend", "inpaint", "replace_element", "style_transfer"])
def test_fallback_seedance_profile_does_not_enable_unadvertised_operations(operation):
    snapshot = frozen_video("https://enterprise.beefapi.com", "seedance-2.5", managed=True)
    with pytest.raises(GenerationError, match="unsupported_video_operation"):
        build_submission(snapshot, edit_request(operation), SEEDANCE)


@pytest.mark.parametrize("operation", ["extend", "inpaint", "replace_element", "style_transfer"])
def test_explicitly_enabled_video_operations_keep_source_wire(operation):
    snapshot = profile_snapshot()
    snapshot["canvas_video_capability"]["operations"] = [operation]
    body = build_submission(snapshot, edit_request(operation), SEEDANCE)[2]
    assert body["seconds"] == ("5" if operation == "extend" else "-1")
    assert body["metadata"]["ratio"] == "adaptive"
    assert body["metadata"]["omni_reference_task_type"] == (
        "extend" if operation == "extend" else "edit"
    )
