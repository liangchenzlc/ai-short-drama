"""文字视频只允许已授权冻结资源；默认 manifest 保持 video_url 顺序。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_canvas_text_references import admission_fixture, prepared_text, text_snapshot

from short_drama.ai import GenerationError
from short_drama.ai.adapters import build_submission
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas_generation_inputs import generation_payload
from short_drama.service.canvas_text_admission import freeze_text_references

ADAPTERS = ["openai_chat.v1", "openai_responses.v1"]
VIDEO_IDS = ["9007199254741001", "9007199254741003"]


def prepared_video(*, mixed=False):
    payload = prepared_text()
    payload["canvas_request"]["input"]["referenceVideos"] = [
        {"storageKey": "resource:" + identifier, "type": "video/mp4"} for identifier in VIDEO_IDS
    ]
    payload["input"]["video_reference_media_ids"] = VIDEO_IDS.copy()
    payload["input"]["video_reference_urls"] = [
        "https://media.example/first.mp4?signature=execution-only",
        "https://media.example/second.webm?signature=execution-only",
    ]
    payload["canvas_text_references"]["videos"] = [
        {"media_id": identifier, "mime_type": mime, "byte_size": size, "duration_ms": duration}
        for identifier, mime, size, duration in zip(
            VIDEO_IDS, ["video/mp4", "video/webm"], [500, 700], [1000, 2000], strict=True
        )
    ]
    if not mixed:
        payload["canvas_request"]["input"]["referenceImages"] = []
        payload["input"]["reference_media_ids"] = []
        payload["input"]["reference_urls"] = []
        payload["canvas_text_references"]["references"] = []
    return payload


def video_snapshot():
    snapshot = text_snapshot()
    snapshot["canvas_text_capability"]["references"].update(maxVideos=2, maxVideoBytes=1024)
    return snapshot


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_video_references_keep_standard_text_placeholder(adapter):
    payload = prepared_video()
    request = payload["canvas_request"]
    before = deepcopy(request)
    result = generation_payload(CanvasRuntimeTaskCreate.model_validate(request), 1, adapter)
    assert result["input"] == {"messages": payload["input"]["messages"]}
    assert request == before


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("mixed", [False, True])
def test_text_video_default_manifest_keeps_text_images_then_videos(adapter, mixed):
    payload = prepared_video(mixed=mixed)
    before = deepcopy(payload)
    _, _, body = build_submission(video_snapshot(), payload, adapter)
    messages = body["input" if adapter == "openai_responses.v1" else "messages"]
    expected = [
        {"type": "text", "text": payload["canvas_request"]["prompt"]},
        *[
            {"type": "image_url", "image_url": {"url": url}}
            for url in payload["input"]["reference_urls"]
        ],
        *[
            {"type": "video_url", "video_url": {"url": url}}
            for url in payload["input"]["video_reference_urls"]
        ],
    ]
    assert messages[-1] == {"role": "user", "content": expected}
    assert payload == before


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize(
    "change", ["ids", "urls", "declared", "mime", "duration", "limit", "bytes"]
)
def test_video_freeze_identity_metadata_and_saved_limits_cannot_be_bypassed(adapter, change):
    payload, snapshot = prepared_video(), video_snapshot()
    if change == "ids":
        payload["input"]["video_reference_media_ids"].reverse()
    elif change == "urls":
        payload["input"]["video_reference_urls"].pop()
    elif change == "declared":
        payload["canvas_request"]["input"]["referenceVideos"].reverse()
    elif change == "mime":
        payload["canvas_text_references"]["videos"][0]["mime_type"] = "image/png"
    elif change == "duration":
        payload["canvas_text_references"]["videos"][0]["duration_ms"] = 0
    elif change == "limit":
        snapshot["canvas_text_capability"]["references"]["maxVideos"] = 1
    else:
        snapshot["canvas_text_capability"]["references"]["maxVideoBytes"] = 600
    before = deepcopy(payload)
    with pytest.raises(GenerationError):
        build_submission(snapshot, payload, adapter)
    assert payload == before


def video_admission(monkeypatch):
    generation, images, _, _ = admission_fixture(monkeypatch)
    payload = prepared_video(mixed=True)
    media = {
        **images,
        **{
            reference["media_id"]: SimpleNamespace(
                storage_locator="minio://private/" + reference["media_id"] + ".mp4",
                format_code=reference["mime_type"],
                byte_size=reference["byte_size"],
                width=160,
                height=90,
                duration_ms=reference["duration_ms"],
                project_id=1,
            )
            for reference in payload["canvas_text_references"]["videos"]
        },
    }
    generation._validate_media = lambda identifier, _kind: media[identifier]
    return (
        generation,
        media,
        payload,
        {"canvas_text_capability": video_snapshot()["canvas_text_capability"]},
    )


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_video_admission_uses_actual_bytes_mime_dimensions_duration_and_omits_urls(
    monkeypatch, adapter
):
    generation, _, payload, cache = video_admission(monkeypatch)
    expected = deepcopy(payload["canvas_text_references"])
    for reference in payload["canvas_request"]["input"]["referenceVideos"]:
        reference.update(
            type="image/png",
            bytes=1,
            width=1,
            height=1,
            durationMs=1,
            url="https://fake.example/video",
        )
    freeze_text_references(generation, payload, 1, adapter, cache)
    assert payload["canvas_text_references"] == expected
    assert payload["input"]["video_reference_media_ids"] == VIDEO_IDS
    normalized = payload["canvas_request"]["input"]["referenceVideos"]
    assert [reference["durationMs"] for reference in normalized] == [1000, 2000]
    assert [reference["bytes"] for reference in normalized] == [500, 700]
    assert [reference["type"] for reference in normalized] == ["video/mp4", "video/webm"]
    assert all(reference["width"] == 160 and reference["height"] == 90 for reference in normalized)
    assert all("url" not in reference for reference in normalized)


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize(
    "failure", ["scope", "locator", "size", "mime", "duration", "count", "unsaved"]
)
def test_failed_second_video_admission_never_partially_rewrites_images_or_videos(
    monkeypatch, adapter, failure
):
    generation, media, payload, cache = video_admission(monkeypatch)
    second = media[VIDEO_IDS[1]]
    if failure == "scope":
        second.project_id = 2
    elif failure == "locator":
        second.storage_locator = "https://untrusted.example/video"
    elif failure == "size":
        second.byte_size = 1025
    elif failure == "mime":
        second.format_code = "image/png"
    elif failure == "duration":
        second.duration_ms = None
    elif failure == "count":
        cache["canvas_text_capability"]["references"]["maxVideos"] = 1
    else:
        payload["canvas_request"]["input"]["referenceVideos"][1]["storageKey"] = "blob:unsaved"
    before = deepcopy(payload)
    with pytest.raises(NotFound if failure == "scope" else WorkflowError):
        freeze_text_references(generation, payload, 1, adapter, cache)
    assert payload == before
