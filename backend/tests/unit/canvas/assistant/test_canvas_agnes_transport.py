"""Agnes 官方合同通过本机 HTTP 复现；这些是协议测试，不是真实供应商验收。"""

import base64
from copy import deepcopy

import pytest

from short_drama.ai.adapters import build_submission, poll_endpoint, validate_request
from short_drama.ai.canvas_credentials import CanvasCredentials
from short_drama.ai.types import GenerationError
from tests.unit.canvas.video.test_canvas_video_transport import OPENAI, frozen_video, video_request
from tests.unit.shared.generation.test_generation_adapters import gateway as gateway
from tests.unit.shared.generation.test_generation_adapters import provider as provider
from tests.unit.shared.generation.test_generation_adapters import reference_image

IMAGE_MODEL = "agnes-image-2.5-flash"
VIDEO_MODEL = "agnes-video-2.5-flash"
IMAGE_ADAPTER = "openai_images.v1"


def image_snapshot(base):
    return {
        **frozen_video(base, IMAGE_MODEL),
        "service_type": "image",
    }


def image_request(references=(), **options):
    return {
        "source": {"scene": "canvas_node"},
        "input": {
            "prompt": "红色礼盒",
            "reference_media_ids": [str(index + 10) for index, _ in enumerate(references)],
            "reference_urls": list(references),
        },
        "parameters": {"count": 1},
        "canvas_parameters": {"mode": "image", "size": "1:1", "quality": "auto", **options},
        "canvas_request": {"input": {"mode": "image"}},
    }


def agnes_video_request(**kwargs):
    request = video_request(**kwargs)
    request["canvas_parameters"] = {"mode": "video"}
    return request


def credential():
    return CanvasCredentials(apiKey="private-key")


@pytest.mark.parametrize("quality,size", [("auto", "1K"), ("2k", "2K"), ("high", "4K")])
def test_image_uses_documented_tier_ratio_and_nested_format(quality, size):
    url, headers, body = build_submission(
        image_snapshot("https://api.agnes-ai.cn/v1"),
        image_request(size="16:9", quality=quality),
        IMAGE_ADAPTER,
    )
    assert url == "https://api.agnes-ai.cn/v1/images/generations"
    assert headers == {}
    assert body == {
        "model": IMAGE_MODEL,
        "prompt": "红色礼盒",
        "size": size,
        "ratio": "16:9",
        "return_base64": True,
        "extra_body": {"response_format": "b64_json"},
    }


def test_image_reads_authorized_bytes_in_order_into_nested_json(gateway, provider):
    base, state, calls = provider
    data = reference_image()
    state["body"] = {"data": [{"b64_json": base64.b64encode(data).decode()}]}
    loaded = []

    def load(index, limit, deadline):
        loaded.append(index)
        return data

    request = image_request(["https://reference.invalid/a", "https://reference.invalid/b"])
    original = deepcopy(request)
    result = gateway.submit(
        image_snapshot(base), request, credential(), IMAGE_ADAPTER, reference_loader=load
    )
    assert result.status == "succeeded"
    assert loaded == [0, 1]
    assert request == original
    assert len(calls) == 1
    assert calls[0][:2] == ("POST", "/v1/images/generations")
    assert calls[0][2]["Content-Type"] == "application/json"
    assert (
        calls[0][3]["extra_body"]["image"]
        == ["data:image/png;base64," + base64.b64encode(data).decode()] * 2
    )
    assert "image" not in calls[0][3] and "response_format" not in calls[0][3]
    assert "extra_body" not in result.resolved_parameters


@pytest.mark.parametrize("options", [{"transparent_background": True}, {"mask_media_id": "20"}])
def test_unsupported_image_options_reject_before_io(gateway, provider, options):
    base, _, calls = provider
    with pytest.raises(GenerationError, match="unsupported_parameters"):
        gateway.submit(image_snapshot(base), image_request(**options), credential(), IMAGE_ADAPTER)
    assert calls == []


def test_video_posts_json_and_uses_video_id_for_future_polls(gateway, provider):
    base, state, calls = provider
    state["body"] = {
        "id": "task-other",
        "task_id": "task-other",
        "video_id": "video-original",
        "status": "queued",
    }
    result = gateway.submit(
        frozen_video(base, VIDEO_MODEL), agnes_video_request(), credential(), OPENAI
    )
    assert result.provider_task_id == "video-original"
    assert result.status == "submitted"
    assert calls[0][:2] == ("POST", "/v1/videos")
    assert calls[0][2]["Content-Type"] == "application/json"
    assert calls[0][3] == {
        "model": VIDEO_MODEL,
        "prompt": "源提示词",
        "mode": "text",
        "seconds": "6",
        "size": "720P",
        "aspect_ratio": "16:9",
    }


def test_video_polls_original_video_identity_at_documented_root(gateway, provider):
    base, state, calls = provider
    state["body"] = {"id": "task-other", "status": "running", "progress": 50}
    result = gateway.poll(
        frozen_video(base + "/v1", VIDEO_MODEL), "video-original", credential(), OPENAI
    )
    assert result.provider_task_id == "video-original"
    assert result.status == "submitted"
    assert calls[0][:2] == ("GET", "/agnesapi?video_id=video-original&model_name=" + VIDEO_MODEL)
    assert len(calls) == 1
    assert (
        poll_endpoint(
            frozen_video("https://api.agnes-ai.cn/v1", VIDEO_MODEL), "video-original", OPENAI
        )
        == "https://api.agnes-ai.cn/agnesapi?video_id=video-original&model_name=" + VIDEO_MODEL
    )


def test_video_completed_uses_public_status_and_top_level_url(gateway, provider):
    base, state, _ = provider
    state["body"] = {
        "id": "task-other",
        "status": "completed",
        "progress": 100,
        "internal_status": "pending",
        "internal_progress": 0,
        "url": "https://cdn.example/result.mp4",
        "error": None,
    }
    result = gateway.poll(frozen_video(base, VIDEO_MODEL), "video-original", credential(), OPENAI)
    assert result.status == "succeeded"
    assert result.provider_task_id == "video-original"
    assert result.outputs == [{"url": "https://cdn.example/result.mp4", "media_type": "video"}]


def test_video_pending_url_does_not_pretend_completion(gateway, provider):
    base, state, _ = provider
    state["body"] = {"status": "pending", "url": "https://cdn.example/unfinished.mp4"}
    result = gateway.poll(frozen_video(base, VIDEO_MODEL), "video-original", credential(), OPENAI)
    assert result.status == "submitted" and result.outputs == []


@pytest.mark.parametrize(
    "reply", [{"id": "task-other"}, {"video_id": "video-original", "status": "completed"}]
)
def test_unusable_video_creation_is_unknown(gateway, provider, reply):
    base, state, calls = provider
    state["body"] = reply
    with pytest.raises(GenerationError) as error:
        gateway.submit(frozen_video(base, VIDEO_MODEL), agnes_video_request(), credential(), OPENAI)
    assert error.value.accepted_unknown
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["keyframe", "reference"])
def test_video_public_references_use_official_fields(mode):
    request = agnes_video_request(
        images=[{"id": "first", "url": "https://cdn.example/first.png"}],
        audios=[{"url": "https://cdn.example/a.mp3"}] if mode == "reference" else [],
        operation="reference_to_video" if mode == "reference" else "image_to_video",
    )
    if mode == "keyframe":
        request["canvas_request"]["input"]["metadata"]["videoStartFrameNodeId"] = "first"
    _, _, body = build_submission(
        frozen_video("https://api.agnes-ai.cn/v1", VIDEO_MODEL), request, OPENAI
    )
    assert body["mode"] == mode
    if mode == "keyframe":
        assert body["first_frame"] == "https://cdn.example/first.png"
        assert "images" not in body
    else:
        assert body["images"] == ["https://cdn.example/first.png"]
        assert body["audios"] == ["https://cdn.example/a.mp3"]


@pytest.mark.parametrize(
    "config",
    [{"vquality": "1080p"}, {"videoSeconds": "3"}, {"videoSeconds": "13"}, {"size": "2:3"}],
)
def test_invalid_flash_video_options_reject_without_post(gateway, provider, config):
    base, _, calls = provider
    with pytest.raises(GenerationError):
        gateway.submit(
            frozen_video(base, VIDEO_MODEL),
            agnes_video_request(config=config),
            credential(),
            OPENAI,
        )
    assert calls == []


def test_local_video_reference_requires_public_link_before_io(gateway, provider):
    base, _, calls = provider
    with pytest.raises(GenerationError, match="reference_media_requires_url"):
        validate_request(
            frozen_video(base, VIDEO_MODEL),
            agnes_video_request(images=[{"storageKey": "resource:12"}], operation="image_to_video"),
            OPENAI,
        )
    assert calls == []


def test_explicit_queue_rejection_has_no_unknown_or_automatic_second_post(gateway, provider):
    base, state, calls = provider
    state.update(
        status=503, body={"code": "video_queue_full", "message": "private-key", "data": None}
    )
    with pytest.raises(GenerationError) as error:
        gateway.submit(frozen_video(base, VIDEO_MODEL), agnes_video_request(), credential(), OPENAI)
    assert error.value.code == "provider_queue_full"
    assert error.value.http_status == 503
    assert error.value.retryable and not error.value.accepted_unknown
    assert "private-key" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "reply",
    [
        {"code": "other"},
        {"code": "video_queue_full", "video_id": "video-original"},
        {"code": "video_queue_full", "data": {"id": "task"}},
    ],
)
def test_other_5xx_still_protects_unknown_acceptance(gateway, provider, reply):
    base, state, calls = provider
    state.update(status=503, body=reply)
    with pytest.raises(GenerationError) as error:
        gateway.submit(frozen_video(base, VIDEO_MODEL), agnes_video_request(), credential(), OPENAI)
    assert error.value.accepted_unknown
    assert len(calls) == 1


@pytest.mark.parametrize(
    "references",
    [
        {"images": [{"url": "https://cdn.example/a.png"}] * 6},
        {"audios": [{"url": "https://cdn.example/a.mp3"}] * 4},
        {"videos": [{"url": "https://cdn.example/a.mp4"}]},
    ],
)
def test_flash_reference_limits_reject_before_io(gateway, provider, references):
    base, _, calls = provider
    with pytest.raises(GenerationError):
        gateway.submit(
            frozen_video(base, VIDEO_MODEL),
            agnes_video_request(operation="reference_to_video", **references),
            credential(),
            OPENAI,
        )
    assert calls == []


def test_completed_without_url_does_not_invent_video_content_endpoint(gateway, provider):
    base, state, calls = provider
    state["body"] = {"id": "task-other", "status": "completed"}
    with pytest.raises(GenerationError, match="missing_output") as error:
        gateway.poll(frozen_video(base, VIDEO_MODEL), "video-original", credential(), OPENAI)
    assert error.value.retryable and not error.value.accepted_unknown
    assert len(calls) == 1 and calls[0][0] == "GET"


def test_mismatched_poll_identity_is_not_adopted(gateway, provider):
    base, state, calls = provider
    state["body"] = {
        "video_id": "another-video",
        "status": "completed",
        "url": "https://cdn.example/a.mp4",
    }
    with pytest.raises(GenerationError, match="invalid_response"):
        gateway.poll(frozen_video(base, VIDEO_MODEL), "video-original", credential(), OPENAI)
    assert len(calls) == 1


def test_unbounded_server_error_body_keeps_unknown(gateway, provider):
    base, state, calls = provider
    state.update(status=503, raw=b" " * 65537)
    with pytest.raises(GenerationError) as error:
        gateway.submit(frozen_video(base, VIDEO_MODEL), agnes_video_request(), credential(), OPENAI)
    assert error.value.accepted_unknown
    assert len(calls) == 1


def test_standard_image_request_does_not_select_canvas_agnes_contract():
    request = {"input": {"prompt": "标准图片"}, "parameters": {"count": 1}}
    _, _, body = build_submission(
        image_snapshot("https://api.agnes-ai.cn/v1"), request, IMAGE_ADAPTER
    )
    assert body == {"model": IMAGE_MODEL, "prompt": "标准图片", "n": 1}
