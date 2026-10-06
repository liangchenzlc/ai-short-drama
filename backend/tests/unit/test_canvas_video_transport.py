"""固定源视频线协议经本机 HTTP；不代表真实供应商验收。"""

from copy import deepcopy

import pytest
from test_generation_adapters import gateway as gateway
from test_generation_adapters import provider as provider
from test_generation_adapters import reference_image

from short_drama.ai.adapters import build_submission, parse_result, poll_endpoint, validate_request
from short_drama.ai.canvas_credentials import CanvasCredentials
from short_drama.ai.types import GenerationError

OPENAI = "canvas_openai_videos.v1"
NEWAPI = "canvas_newapi_video_generations.v1"
SEEDANCE = "canvas_beefapi_seedance_video.v1"


def frozen_video(base, model="sora", *, managed=False):
    return {
        "base_url": base,
        "service_type": "video",
        "model_key": model,
        "budget_seconds": 5,
        "canvas_auth_version": 1,
        "canvas_auth_scene": "canvas_node",
        **({"canvas_channel_key": "beefapi"} if managed else {}),
    }


def video_request(*, images=(), videos=(), audios=(), operation="text_to_video", config=None):
    return {
        "source": {"scene": "canvas_node"},
        "input": {"prompt": "源提示词", "reference_media_ids": []},
        "parameters": {},
        "canvas_parameters": {"mode": "video", "generate_audio": True},
        "canvas_request": {
            "type": "canvas_video",
            "operation": operation,
            "input": {
                "mode": "video",
                "prompt": "源提示词",
                "config": {
                    "videoSeconds": "6",
                    "size": "16:9",
                    "vquality": "720p",
                    **(config or {}),
                },
                "referenceImages": list(images),
                "referenceVideos": list(videos),
                "referenceAudios": list(audios),
                "metadata": {"videoEditOperation": operation},
            },
        },
    }


def canvas_credential():
    return CanvasCredentials(
        apiKey="private-key", headers=[{"name": "X-Private", "value": "private-header"}]
    )


def test_openai_video_uses_single_multipart_reference_and_original_fields(gateway, provider):
    base, state, calls = provider
    state["body"] = {"id": "original-job", "status": "queued"}
    data = reference_image()
    observed = []

    def load(kind, index, limit, deadline):
        observed.append((kind, index))
        return data

    payload = video_request(
        images=[{"storageKey": "resource:12"}, {"storageKey": "resource:13"}],
        operation="image_to_video",
    )
    result = gateway.submit(
        frozen_video(base), payload, canvas_credential(), OPENAI, canvas_reference_loader=load
    )
    assert result.status == "submitted" and result.provider_task_id == "original-job"
    assert observed == [("image", 0)]
    assert len(calls) == 1 and calls[0][:2] == ("POST", "/v1/videos")
    fields = calls[0][3]
    values = {name: value.decode() for name, filename, _, value in fields if filename is None}
    assert values == {
        "model": "sora",
        "prompt": "源提示词",
        "seconds": "6",
        "size": "16:9",
        "resolution_name": "720p",
    }
    assert [
        (name, filename, mime, value) for name, filename, mime, value in fields if filename
    ] == [("input_reference", "input-reference.png", "image/png", data)]
    assert calls[0][2]["Authorization"] == "Bearer private-key"
    assert calls[0][2]["X-Private"] == "private-header"
    assert "Idempotency-Key" not in calls[0][2]


def test_channel_two_json_preserves_all_reference_kinds_and_order(gateway, provider):
    base, state, calls = provider
    state["body"] = {"data": {"task_id": "channel-two-job", "status": "running"}}
    payload = video_request(
        images=[{"url": "https://cdn.example/b.png"}, {"url": "https://cdn.example/a.png"}],
        videos=[{"url": "https://cdn.example/c.mp4"}],
        audios=[{"url": "https://cdn.example/d.wav"}],
        operation="reference_to_video",
    )
    result = gateway.submit(
        frozen_video(base, "custom-video"), payload, canvas_credential(), NEWAPI
    )
    assert result.provider_task_id == "channel-two-job"
    assert calls[0][:2] == ("POST", "/v1/video/generations")
    assert calls[0][3] == {
        "model": "custom-video",
        "prompt": "源提示词",
        "seconds": "6",
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "generate_audio": True,
        "image_urls": ["https://cdn.example/b.png", "https://cdn.example/a.png"],
        "video_urls": ["https://cdn.example/c.mp4"],
        "audio_urls": ["https://cdn.example/d.wav"],
    }


@pytest.mark.parametrize("adapter", [OPENAI, NEWAPI, SEEDANCE])
def test_new_adapters_reject_standard_request_without_network(gateway, provider, adapter):
    base, _, calls = provider
    with pytest.raises(GenerationError):
        gateway.submit(
            frozen_video(base), {"input": {"prompt": "standard"}}, canvas_credential(), adapter
        )
    assert calls == []


def test_channel_two_local_reference_is_rejected_by_pure_admission():
    with pytest.raises(GenerationError, match="reference_media_requires_url"):
        validate_request(
            frozen_video("https://custom.example"),
            video_request(images=[{"storageKey": "resource:12"}], operation="image_to_video"),
            NEWAPI,
        )


@pytest.mark.parametrize(
    "adapter,path",
    [
        (OPENAI, "/v1/videos/original-job"),
        (NEWAPI, "/v1/video/generations/original-job"),
        (SEEDANCE, "/v1/videos/original-job"),
    ],
)
def test_poll_only_queries_original_task_without_creating(gateway, provider, adapter, path):
    base, state, calls = provider
    state["body"] = {"id": "original-job", "status": "running"}
    settings = frozen_video(
        base, "seedance-2.0" if adapter == SEEDANCE else "model", managed=adapter == SEEDANCE
    )
    if adapter == SEEDANCE:
        gateway.settings.canvas_beefapi_test_origin = base
    result = gateway.poll(settings, "original-job", canvas_credential(), adapter)
    assert result.status == "submitted"
    assert len(calls) == 1 and calls[0][:2] == ("GET", path)


def test_openai_completed_without_url_uses_content_without_fake_success(gateway, provider):
    base, state, calls = provider
    state["body"] = {"id": "original-job", "status": "completed"}
    result = gateway.poll(frozen_video(base), "original-job", canvas_credential(), OPENAI)
    assert result.outputs == [
        {"url": base + "/v1/videos/original-job/content", "media_type": "video"}
    ]
    assert calls[0][2]["X-Private"] == "private-header"


def test_channel_two_completed_without_url_has_no_content_fallback(gateway, provider):
    base, state, calls = provider
    state["body"] = {"id": "original-job", "status": "completed"}
    with pytest.raises(GenerationError, match="missing_output"):
        gateway.poll(frozen_video(base), "original-job", canvas_credential(), NEWAPI)
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["SUCCESS", "succeeded", "complete", "done"])
def test_nested_channel_two_result_and_status_follow_manifest(status):
    result = parse_result(
        {
            "data": {
                "status": status,
                "data": {"metadata": {"url": "https://cdn.example/result.mp4"}},
            }
        },
        NEWAPI,
        submitted=False,
        task_id="original-job",
    )
    assert result.status == "succeeded" and result.outputs[0]["url"].endswith("/result.mp4")


def test_create_missing_or_invalid_id_is_unknown_and_never_second_post(gateway, provider):
    base, state, calls = provider
    for body in ({"status": "queued"}, {"id": {"unsafe": 1}, "status": "queued"}):
        calls.clear()
        state["body"] = body
        with pytest.raises(GenerationError) as error:
            gateway.submit(frozen_video(base), video_request(), canvas_credential(), OPENAI)
        assert error.value.accepted_unknown and len(calls) == 1


def test_provider_secret_cannot_become_durable_task_identifier(gateway, provider):
    base, state, calls = provider
    state["body"] = {"id": "private-header", "status": "queued"}
    with pytest.raises(GenerationError) as error:
        gateway.submit(frozen_video(base), video_request(), canvas_credential(), OPENAI)
    assert error.value.accepted_unknown and len(calls) == 1


def test_seedance_twenty_five_first_last_and_edit_options_keep_source_wire():
    settings = frozen_video("https://enterprise.beefapi.com", "seedance-2.5", managed=True)
    payload = video_request(
        images=[
            {"id": "first", "url": "https://cdn.example/first.png"},
            {"id": "last", "url": "https://cdn.example/last.png"},
        ],
        operation="image_to_video",
    )
    _, _, body = build_submission(settings, payload, SEEDANCE)
    assert body["seconds"] == "6" and body["metadata"]["ratio"] == "adaptive"
    assert [item["role"] for item in body["content"]] == ["first_frame", "last_frame"]
    edited = video_request(videos=[{"url": "https://cdn.example/clip.mp4"}], operation="inpaint")
    settings["canvas_video_capability"] = {
        "operations": ["inpaint"],
        "generateAudio": {"supported": True},
        "watermark": {"supported": True},
    }
    _, _, body = build_submission(settings, edited, SEEDANCE)
    assert body["seconds"] == "-1"
    assert body["metadata"] == {
        "ratio": "adaptive",
        "generate_audio": True,
        "omni_reference_task_type": "edit",
        "watermark": False,
    }


def test_seedance_twenty_single_frame_omits_ratio_and_content():
    _, _, body = build_submission(
        frozen_video("https://enterprise.beefapi.com", "seedance-2.0", managed=True),
        video_request(
            images=[{"url": "https://cdn.example/first.png"}], operation="image_to_video"
        ),
        SEEDANCE,
    )
    assert body == {
        "model": "seedance-2.0",
        "prompt": "源提示词",
        "duration": 6,
        "resolution": "720p",
        "generate_audio": True,
        "image": {"url": "https://cdn.example/first.png"},
    }


def test_seedance_twenty_five_accepts_thirty_images_but_not_thirty_one():
    settings = frozen_video("https://enterprise.beefapi.com", "seedance-2.5", managed=True)
    payload = video_request(
        images=[{"url": f"https://cdn.example/{i}.png"} for i in range(30)],
        operation="reference_to_video",
    )
    assert len(build_submission(settings, payload, SEEDANCE)[2]["content"]) == 30
    excessive = deepcopy(payload)
    excessive["canvas_request"]["input"]["referenceImages"].append(
        {"url": "https://cdn.example/extra.png"}
    )
    with pytest.raises(GenerationError, match="reference_limit"):
        build_submission(settings, excessive, SEEDANCE)


def test_newapi_endpoint_preserves_explicit_version_without_double_v1():
    for base in (
        "https://custom.example",
        "https://custom.example/v1",
        "https://custom.example/v3",
    ):
        assert (
            poll_endpoint(frozen_video(base), "original-job", OPENAI)
            == "https://custom.example/v1/videos/original-job"
        )


def test_client_variants_and_arbitrary_provider_options_are_not_forwarded():
    payload = video_request()
    payload["canvas_request"]["input"]["metadata"]["providerOptions"] = {
        "newapi": {"variants": "client", "secret": "client"}
    }
    settings = frozen_video("https://custom.example")
    settings["canvas_video_variants"] = 2
    body = build_submission(settings, payload, OPENAI)[2]
    assert body["variants"] == 2 and "client" not in str(body)


@pytest.mark.parametrize(
    "extra", [{"thinking": True}, {"providerOptions": {"secret": "untrusted"}}, {"temperature": 1}]
)
def test_video_parameters_are_closed_before_network(gateway, provider, extra):
    base, _, calls = provider
    payload = video_request()
    payload["canvas_parameters"].update(extra)
    with pytest.raises(GenerationError, match="invalid_canvas_video"):
        gateway.submit(frozen_video(base), payload, canvas_credential(), OPENAI)
    assert calls == []


@pytest.mark.parametrize(
    "profile, expected",
    [
        (
            {"generateAudio": {"supported": False}, "watermark": {"supported": False}},
            {"ratio": "adaptive"},
        ),
        (
            {"generateAudio": {"supported": True}, "watermark": {"supported": True}},
            {"ratio": "adaptive", "generate_audio": True, "watermark": False},
        ),
    ],
)
def test_seedance_boolean_fields_follow_frozen_capability(profile, expected):
    settings = frozen_video("https://enterprise.beefapi.com", "seedance-2.5", managed=True)
    settings["canvas_video_capability"] = profile
    payload = video_request(
        images=[{"url": "https://cdn.example/frame.png"}], operation="image_to_video"
    )
    payload["canvas_parameters"] = {"mode": "video"}
    assert build_submission(settings, payload, SEEDANCE)[2]["metadata"] == expected


@pytest.mark.parametrize(
    "kind,item,refs",
    [
        ("images", {"width": 299, "height": 500}, {"minImageWidth": 300}),
        ("images", {"width": 1000, "height": 300}, {"maxImageAspect": 2.5}),
        ("videos", {"width": 640, "height": 640}, {"minVideoPixels": 409601}),
        ("videos", {}, {"minVideoPixels": 409600}),
        ("videos", {"durationMs": 1999}, {"minVideoDurationSeconds": 2}),
        ("audios", {"durationMs": 1799}, {"minAudioDurationSeconds": 1.8}),
        ("videos", {"durationMs": 16000}, {"maxVideoDurationSeconds": 15}),
        ("audios", {"durationMs": 31000}, {"maxAudioTotalDurationSeconds": 30}),
    ],
)
def test_trusted_reference_constraints_reject_before_media_read(kind, item, refs):
    settings = frozen_video("https://enterprise.beefapi.com", "seedance-2.5", managed=True)
    settings["canvas_video_capability"] = {"references": refs}
    payload = video_request(
        **{kind: [{"storageKey": "resource:12", **item}]}, operation="reference_to_video"
    )
    with pytest.raises(GenerationError, match="invalid_reference"):
        build_submission(settings, payload, SEEDANCE)


def test_reference_total_duration_and_opaque_asset_contract():
    settings = frozen_video("https://enterprise.beefapi.com", "seedance-2.5", managed=True)
    settings["canvas_video_capability"] = {
        "references": {
            "minVideoDurationSeconds": 2,
            "maxVideoDurationSeconds": 30,
            "maxVideoTotalDurationSeconds": 30,
            "minVideoPixels": 409600,
        }
    }
    opaque = video_request(videos=[{"url": "asset://opaque-123"}], operation="reference_to_video")
    assert (
        build_submission(settings, opaque, SEEDANCE)[2]["content"][0]["video_url"]["url"]
        == "asset://opaque-123"
    )
    excessive = video_request(
        videos=[
            {
                "url": "https://cdn.example/video.mp4",
                "width": 1000,
                "height": 600,
                "durationMs": 16000,
            }
        ]
        * 2,
        operation="reference_to_video",
    )
    with pytest.raises(GenerationError, match="invalid_reference"):
        build_submission(settings, excessive, SEEDANCE)


@pytest.mark.parametrize(
    "status,body,expected,retryable",
    [
        (
            400,
            {"error": {"code": "task_not_exist", "message": "private"}},
            "provider_task_not_ready",
            True,
        ),
        (400, {"data": {"message": "TASK NOT FOUND"}}, "provider_task_not_ready", True),
        (400, {"error": {"code": "bad_input", "message": "private"}}, "provider_rejected", False),
        (404, {}, "provider_endpoint", True),
        (401, {}, "provider_auth", False),
        (403, {}, "provider_auth", False),
        (429, {}, "provider_rate_limit", True),
        (503, {}, "upstream_unavailable", True),
    ],
)
def test_canvas_video_poll_http_retry_classification(
    gateway, provider, status, body, expected, retryable
):
    base, state, calls = provider
    state.update(status=status, body=body, headers={"Retry-After": "47"})
    with pytest.raises(GenerationError) as error:
        gateway.poll(frozen_video(base), "original-job", canvas_credential(), OPENAI)
    assert error.value.code == expected and error.value.retryable is retryable
    assert error.value.http_status == status and error.value.retry_after == 47
    assert not error.value.accepted_unknown and "private" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("external", [False, True])
def test_canvas_video_download_error_keeps_retry_after_without_credentials_leak(
    gateway, provider, external
):
    base, state, calls = provider
    state.update(status=429, headers={"Retry-After": "51"})
    settings = frozen_video(base if not external else "https://provider.example")
    settings["capability_cache"] = {"adapter": OPENAI}
    with pytest.raises(GenerationError) as error:
        gateway.download_media(
            base + "/result.mp4", 1024, snapshot=settings, credential=canvas_credential()
        )
    assert error.value.http_status == 429 and error.value.retry_after == 51
    assert error.value.retryable
    assert ("Authorization" in calls[0][2]) is not external


def test_canvas_content_download_requests_video_and_fresh_credentials(gateway, provider):
    base, state, calls = provider
    state["raw"] = b"video-data"
    state["headers"] = {"Content-Type": "video/mp4"}
    settings = frozen_video(base)
    settings["capability_cache"] = {"adapter": OPENAI}
    assert gateway.download_media(
        base + "/v1/videos/original-job/content",
        1024,
        snapshot=settings,
        credential=canvas_credential(),
    ) == (b"video-data", "video/mp4")
    assert calls[0][2]["Accept"] == "video/mp4"


def test_retry_after_accepts_future_http_date_and_ignores_invalid():
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    from short_drama.ai.transport import response_retry_after

    at = format_datetime(datetime.now(UTC) + timedelta(seconds=65), usegmt=True)
    assert 63 < response_retry_after({"retry-after": at}) <= 65
    for value in ("-1", "nan", "invalid", "0", "9" * 129):
        assert response_retry_after({"Retry-After": value}) is None


def test_invalid_output_after_paid_create_remains_unknown(gateway, provider):
    base, state, calls = provider
    state["body"] = {"id": "original-job", "status": "completed", "url": "javascript:unsafe"}
    with pytest.raises(GenerationError) as error:
        gateway.submit(frozen_video(base), video_request(), canvas_credential(), OPENAI)
    assert error.value.accepted_unknown and len(calls) == 1


def test_seedance_video_urls_preserve_explicit_base_version():
    settings = frozen_video("https://enterprise.beefapi.com/v3", "seedance-2.0", managed=True)
    assert (
        build_submission(settings, video_request(), SEEDANCE)[0]
        == "https://enterprise.beefapi.com/v3/videos"
    )
    assert (
        poll_endpoint(settings, "original-job", SEEDANCE)
        == "https://enterprise.beefapi.com/v3/videos/original-job"
    )
