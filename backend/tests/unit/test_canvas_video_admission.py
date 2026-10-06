"""画布视频参考准入边界；标准输入上限不随迁移放宽。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.core.exceptions import GenerationRequestError, NotFound, WorkflowError
from short_drama.schemas.ai_generation import VideoInput
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas_generation_inputs import generation_payload
from short_drama.service.canvas_model_catalog_service import protocol_adapter
from short_drama.service.canvas_video_admission import freeze_video_references


def request(kind="video", count=1):
    return CanvasRuntimeTaskCreate.model_validate(
        {
            "projectId": "canvas",
            "type": f"canvas_{kind}",
            "operation": "reference_to_video" if kind == "video" else kind,
            "prompt": "原版参考",
            "logicalModelId": "17",
            "input": {
                "mode": kind,
                "prompt": "原版参考",
                "referenceImages": [
                    {"id": f"image-{index}", "storageKey": f"resource:{index + 1}"}
                    for index in range(count)
                ],
                "metadata": {"nodeId": "target", "clientOperationId": "video-admission"},
            },
        }
    )


def generation(monkeypatch, *, scope=(None, 9), locator="minio://bucket/reference"):
    monkeypatch.setattr(
        "short_drama.service.canvas_video_admission.scope_of", lambda session, media: scope
    )
    media = SimpleNamespace(
        storage_locator=locator,
        format_code="image/png",
        byte_size=512,
        width=1024,
        height=768,
        duration_ms=None,
    )
    return SimpleNamespace(session=object(), _validate_media=lambda identifier, kind: media)


def test_canvas_video_accepts_thirty_images_and_standard_still_rejects_ten():
    assert len(request(count=30).input.reference_images) == 30
    with pytest.raises(ValidationError):
        request(count=31)
    with pytest.raises(ValidationError):
        VideoInput(prompt="标准", reference_media_ids=[str(index + 1) for index in range(10)])
    with pytest.raises(ValidationError):
        request("image", 17)


@pytest.mark.parametrize(
    "adapter",
    [
        "canvas_openai_videos.v1",
        "canvas_newapi_video_generations.v1",
        "canvas_beefapi_seedance_video.v1",
    ],
)
@pytest.mark.parametrize("enabled", [True, "true"])
def test_original_global_ark_upload_default_is_inactive_on_other_video_protocols(adapter, enabled):
    value = request()
    value.input.config["videoArkPrivateAssetUpload"] = enabled
    result = generation_payload(value, 9, adapter)
    assert result["input"]["prompt"] == value.prompt
    assert "videoArkPrivateAssetUpload" not in result.get("parameters", {})


def test_resource_metadata_is_server_authoritative_and_original_request_is_retained(monkeypatch):
    original = request().model_dump(mode="json", by_alias=True, exclude_none=True)
    original["input"]["referenceImages"][0].update(
        bytes=1, type="video/mp4", width=1, height=1, durationMs=999
    )
    before = deepcopy(original)
    prepared = {"canvas_request": deepcopy(original), "input": {"prompt": "原版参考"}}
    freeze_video_references(generation(monkeypatch), prepared, 9, "canvas_openai_videos.v1")
    reference = prepared["canvas_request"]["input"]["referenceImages"][0]
    assert reference["type"] == "image/png" and reference["bytes"] == 512
    assert reference["width"] == 1024 and reference["height"] == 768
    assert "durationMs" not in reference
    assert prepared["input"]["reference_media_ids"] == ["1"]
    assert original == before


@pytest.mark.parametrize("scope", [(None, 10), (3, None)])
def test_cross_scope_resources_are_rejected_before_admission(monkeypatch, scope):
    prepared = {"canvas_request": request().model_dump(mode="json", by_alias=True), "input": {}}
    with pytest.raises(NotFound):
        freeze_video_references(
            generation(monkeypatch, scope=scope), prepared, 9, "canvas_openai_videos.v1"
        )


def test_temporary_resource_locator_is_rejected(monkeypatch):
    prepared = {"canvas_request": request().model_dump(mode="json", by_alias=True), "input": {}}
    with pytest.raises(WorkflowError) as error:
        freeze_video_references(
            generation(monkeypatch, locator="https://temporary.test/signed"),
            prepared,
            9,
            "canvas_openai_videos.v1",
        )
    assert error.value.code == "canvas_reference_not_saved"


def test_custom_channel_two_requests_source_https_dialog_before_creating_task(monkeypatch):
    prepared = {"canvas_request": request().model_dump(mode="json", by_alias=True), "input": {}}
    with pytest.raises(WorkflowError) as error:
        freeze_video_references(
            generation(monkeypatch), prepared, 9, "canvas_newapi_video_generations.v1"
        )
    assert error.value.code == "reference_media_requires_url"


@pytest.mark.parametrize(
    "url",
    [
        "http://public.test/image.png",
        "https://user:password@public.test/a",
        "data:image/png;base64,a",
    ],
)
def test_external_reference_url_is_https_without_embedded_credentials(monkeypatch, url):
    original = request().model_dump(mode="json", by_alias=True)
    original["input"]["referenceImages"] = [{"url": url}]
    with pytest.raises(WorkflowError):
        freeze_video_references(
            generation(monkeypatch),
            {"canvas_request": original, "input": {}},
            9,
            "canvas_newapi_video_generations.v1",
        )


def test_external_https_reference_is_frozen_without_treating_it_as_a_saved_resource(monkeypatch):
    original = request().model_dump(mode="json", by_alias=True)
    original["input"]["referenceImages"] = [{"url": "https://public.test/image.png?q=1"}]
    prepared = {"canvas_request": original, "input": {}}
    freeze_video_references(
        generation(monkeypatch), prepared, 9, "canvas_newapi_video_generations.v1"
    )
    assert prepared["input"]["reference_media_ids"] == []
    assert original["input"]["referenceImages"][0]["url"].endswith("?q=1")


def test_canvas_thirty_image_placeholder_does_not_pass_through_standard_nine_image_dto():
    value = request(count=30)
    payload = generation_payload(value, 9, "canvas_beefapi_seedance_video.v1")
    assert payload["input"] == {"prompt": value.prompt, "reference_media_ids": []}
    assert payload["config_id"] == "17"
    assert len(value.input.reference_images) == 30


@pytest.mark.parametrize("protocol", ["newapi", "openai-video", "openai-videos"])
def test_old_video_aliases_select_same_official_canvas_adapter(protocol):
    assert (
        protocol_adapter(
            {"id": "custom", "interfaceType": protocol}, {"model": "sora", "capability": "video"}
        )
        == "canvas_openai_videos.v1"
    )


def test_seedance_managed_special_branch_precedes_legacy_protocol_metadata():
    profile = {"model": "doubao-seedance-2.5", "capability": "video", "protocol": "newapi"}
    assert protocol_adapter({"id": "beefapi"}, profile) == "canvas_beefapi_seedance_video.v1"
    assert protocol_adapter({"id": "custom"}, profile) == "canvas_openai_videos.v1"


def test_capability_match_options_override_only_source_video_parameters():
    value = request()
    value.input.config = {"videoSeconds": "5", "size": "16:9", "videoGenerateAudio": "true"}
    value.input.capability_options = {"videoSeconds": 5, "size": "16:9", "videoGenerateAudio": True}
    generation_payload(value, 9, "canvas_openai_videos.v1")
    value.input.capability_options["videoSeconds"] = 10
    generation_payload(value, 9, "canvas_openai_videos.v1")
    value.input.capability_options["privateOption"] = "private-option"
    with pytest.raises(GenerationRequestError):
        generation_payload(value, 9, "canvas_openai_videos.v1")


def test_wan_managed_inline_exception_retains_owned_media_identity(monkeypatch):
    original = request().model_dump(mode="json", by_alias=True)
    prepared = {"canvas_request": original, "input": {}}
    freeze_video_references(
        generation(monkeypatch),
        prepared,
        9,
        "canvas_newapi_video_generations.v1",
        channel_key="beefapi",
        model="wan3.0-video",
    )
    assert prepared["input"]["reference_media_ids"] == ["1"]


def test_mixed_media_loader_uses_original_reference_index(monkeypatch):
    from short_drama.service.canvas_video_references import StoredCanvasVideoReferences

    captures = []

    def loader(factory, storage, settings, identifiers):
        captures.append(identifiers)
        return lambda index, maximum, deadline: identifiers[index].encode()

    monkeypatch.setattr("short_drama.service.canvas_video_references.StoredImageReferences", loader)
    request_value = {
        "canvas_request": {
            "input": {
                "referenceImages": [
                    {"url": "https://public.test/a"},
                    {"storageKey": "resource:17"},
                    {"url": "https://public.test/b"},
                    {"storageKey": "resource:19"},
                ],
                "referenceVideos": [{"storageKey": "resource:23"}],
            }
        }
    }
    stored = StoredCanvasVideoReferences(None, None, None, request_value)
    assert stored("image", 1, 1024, 1.0) == b"17"
    assert stored("image", 3, 1024, 1.0) == b"19"
    assert stored("video", 0, 1024, 1.0) == b"23"
    assert captures == [["17", "19"], ["23"]]
