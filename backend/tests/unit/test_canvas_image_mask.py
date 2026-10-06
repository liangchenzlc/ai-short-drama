"""源蒙版编辑的稳定资源与真实 multipart 合同；不调用付费供应商。"""

from io import BytesIO

import pytest
from PIL import Image
from test_canvas_generation_parameters import frozen_request, request
from test_generation_adapters import gateway as gateway
from test_generation_adapters import provider as provider
from test_generation_adapters import reference_image, snapshot

from short_drama.ai import GenerationError
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_task_runtime import CanvasTaskReference
from short_drama.service.canvas_generation_inputs import generation_payload


def masked_request(*, source=True, mask_key="resource:13"):
    value = request("image", references=[{"storageKey": "resource:12"}] if source else [])
    value.input.mask = CanvasTaskReference(storage_key=mask_key, type="image/png")
    return value


def mask_image(size=(4, 4), *, alpha=True, selected=True):
    image = Image.new("RGBA" if alpha else "RGB", size, "white")
    if alpha and selected:
        image.putpixel((1, 1), (255, 255, 255, 0))
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def test_saved_mask_is_frozen_separately_from_source_image_references():
    payload = frozen_request(masked_request(), "openai_images.v1")
    assert payload["input"]["reference_media_ids"] == ["12"]
    assert payload["canvas_parameters"]["mask_media_id"] == "13"
    assert "mask" not in payload["input"] and "mask_media_id" not in payload["parameters"]


def test_canvas_mask_edits_use_original_image_and_mask_multipart_parts(gateway, provider):
    base, state, calls = provider
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}]}
    payload = frozen_request(masked_request(), "openai_images.v1")
    payload["input"]["reference_urls"] = ["https://reference.invalid/0"]
    source, mask = reference_image(), mask_image()
    result = gateway.submit(
        snapshot(base, "image", "gpt-image-2"),
        payload,
        "fixture-secret",
        "openai_images.v1",
        reference_loader=lambda *_: source,
        mask_reference_loader=lambda *_: mask,
    )
    assert len(calls) == 1 and calls[0][1] == "/v1/images/edits"
    files = [(name, mime, data) for name, filename, mime, data in calls[0][3] if filename]
    assert files == [("image", "image/png", source), ("mask", "image/png", mask)]
    assert "mask" not in result.resolved_parameters and result.status == "succeeded"


@pytest.mark.parametrize(
    "mask,code",
    [
        (mask_image((3, 4)), "mask_dimensions_mismatch"),
        (mask_image(alpha=False), "invalid_mask_image"),
        (mask_image(selected=False), "invalid_mask_image"),
        (b"invalid private-mask", "invalid_mask_image"),
    ],
)
def test_invalid_mask_bytes_never_submit_or_fetch_a_reference_url(gateway, provider, mask, code):
    base, _, calls = provider
    payload = frozen_request(masked_request(), "openai_images.v1")
    payload["input"]["reference_urls"] = [base + "/must-not-fetch"]
    with pytest.raises(GenerationError, match=code) as error:
        gateway.submit(
            snapshot(base, "image", "gpt-image-2"),
            payload,
            "fixture-secret",
            "openai_images.v1",
            reference_loader=lambda *_: reference_image(),
            mask_reference_loader=lambda *_: mask,
        )
    assert not calls and not error.value.accepted_unknown
    assert "private-mask" not in str(error.value)


@pytest.mark.parametrize("adapter", ["ark_images.v1", "dashscope_images.v1"])
def test_channels_without_source_mask_protocol_are_rejected_before_admission(adapter):
    with pytest.raises(WorkflowError, match="mask") as error:
        generation_payload(masked_request(), 2, adapter)
    assert error.value.status_code == 422


def test_mask_requires_saved_resource_and_source_image():
    for value in (masked_request(source=False), masked_request(mask_key="blob:private-mask")):
        with pytest.raises(WorkflowError) as error:
            generation_payload(value, 2, "openai_images.v1")
        assert error.value.status_code == 422 and "private-mask" not in error.value.message


def test_missing_mask_loader_is_known_failure_before_any_provider_call(gateway, provider):
    base, _, calls = provider
    payload = frozen_request(masked_request(), "openai_images.v1")
    payload["input"]["reference_urls"] = ["https://reference.invalid/0"]
    with pytest.raises(GenerationError, match="unresolved_media_reference") as error:
        gateway.submit(
            snapshot(base, "image", "gpt-image-2"),
            payload,
            "fixture-secret",
            "openai_images.v1",
            reference_loader=lambda *_: reference_image(),
        )
    assert not calls and not error.value.accepted_unknown


def test_mask_counts_toward_aggregate_image_budget(gateway, provider, monkeypatch):
    from short_drama.ai import gateway as gateway_module

    base, _, calls = provider
    source, mask = reference_image(), mask_image()
    monkeypatch.setattr(gateway_module, "MAX_REFERENCE_TOTAL_BYTES", len(source) + len(mask) - 1)
    payload = frozen_request(masked_request(), "openai_images.v1")
    payload["input"]["reference_urls"] = ["https://reference.invalid/0"]
    with pytest.raises(GenerationError, match="reference_images_too_large"):
        gateway.submit(
            snapshot(base, "image", "gpt-image-2"),
            payload,
            "fixture-secret",
            "openai_images.v1",
            reference_loader=lambda *_: source,
            mask_reference_loader=lambda *_: mask,
        )
    assert not calls


def test_mask_is_last_part_after_all_original_ordered_source_images(gateway, provider):
    base, state, calls = provider
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}]}
    value = masked_request()
    value.input.reference_images.append(CanvasTaskReference(storage_key="resource:14"))
    payload = frozen_request(value, "openai_images.v1")
    payload["input"]["reference_urls"] = [
        "https://reference.invalid/0",
        "https://reference.invalid/1",
    ]
    sources = [reference_image(), reference_image("JPEG", "blue")]
    mask = mask_image()
    gateway.submit(
        snapshot(base, "image", "gpt-image-2"),
        payload,
        "fixture-secret",
        "openai_images.v1",
        reference_loader=lambda index, *_: sources[index],
        mask_reference_loader=lambda *_: mask,
    )
    assert [(name, mime, data) for name, filename, mime, data in calls[0][3] if filename] == [
        ("image", "image/png", sources[0]),
        ("image", "image/jpeg", sources[1]),
        ("mask", "image/png", mask),
    ]
