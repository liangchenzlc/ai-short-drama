"""画布图片参数通过真实本地 HTTP；不代表真实供应商或媒体归档验收。"""

import base64
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_canvas_generation_parameters import frozen_request, request
from test_generation_adapters import gateway as gateway
from test_generation_adapters import provider as provider
from test_generation_adapters import reference_image, snapshot
from test_generation_references import references as references

from short_drama.ai import GenerationError


def test_canvas_openai_image_parameters_reach_real_json_transport(gateway, provider):
    base, state, calls = provider
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}]}
    payload = frozen_request(
        request("image", {"size": "2752x1536", "quality": "2k", "transparentBackground": True}),
        "openai_images.v1",
    )
    result = gateway.submit(snapshot(base, "image", "gpt-image-2"), payload, "fixture-secret")
    assert calls[0][1] == "/v1/images/generations"
    assert calls[0][3] == {
        "model": "gpt-image-2",
        "prompt": "原版提示词",
        "n": 1,
        "size": "2752x1536",
        "quality": "medium",
        "background": "transparent",
        "output_format": "png",
    }
    assert result.status == "succeeded" and result.outputs[0]["media_type"] == "image"


def test_canvas_openai_image_options_survive_real_multipart_edits(gateway, provider):
    base, state, calls = provider
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}]}
    payload = frozen_request(
        request(
            "image",
            {"size": "16:9", "quality": "4k", "transparentBackground": "true"},
            references=[{"storageKey": "resource:12"}],
        ),
        "openai_images.v1",
    )
    payload["input"]["reference_urls"] = [base + "/stable-private-source"]
    png = reference_image()

    def load_reference(index, limit, deadline):
        assert index == 0 and limit > len(png) and deadline > 0
        return png

    result = gateway.submit(
        snapshot(base, "image", "gpt-image-2"),
        payload,
        "fixture-secret",
        reference_loader=load_reference,
    )
    assert len(calls) == 1 and calls[0][1] == "/v1/images/edits"
    fields = calls[0][3]
    values = {name: data.decode() for name, filename, _, data in fields if filename is None}
    assert values["size"] == "1824x1024" and values["quality"] == "high"
    assert values["background"] == "transparent" and values["output_format"] == "png"
    assert [(mime, data) for _, filename, mime, data in fields if filename] == [("image/png", png)]
    assert [name for name, filename, _, _ in fields if filename] == ["image"]
    assert result.status == "succeeded"


def test_canvas_ark_manifest_options_reach_real_json_transport(gateway, provider):
    base, state, calls = provider
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}]}
    payload = frozen_request(
        request("image", {"size": "2752x1536", "quality": "2k"}), "ark_images.v1"
    )
    result = gateway.submit(
        snapshot(base, "image", "doubao-seedream-5-0"), payload, "fixture-secret", "ark_images.v1"
    )
    assert calls[0][1] == "/api/v3/images/generations"
    assert calls[0][3]["size"] == "2752x1536"
    assert calls[0][3]["response_format"] == "b64_json" and calls[0][3]["watermark"] is False
    assert "quality" not in calls[0][3] and "stream" not in calls[0][3]
    assert result.status == "succeeded"


def test_canvas_qwen_async_submit_and_poll_use_original_task_over_real_http(gateway, provider):
    base, state, calls = provider
    state["body"] = {"output": {"task_id": "qwen-original", "task_status": "PENDING"}}
    settings = snapshot(base + "/compatible-mode/v1", "image", "qwen-image-3.0-pro")
    payload = frozen_request(request("image", {"size": "3:4"}), "dashscope_images.v1")
    result = gateway.submit(settings, payload, "fixture-secret", "dashscope_images.v1")
    assert calls[0][1] == "/api/v1/services/aigc/image-generation/generation"
    assert calls[0][2]["X-DashScope-Async"] == "enable"
    assert calls[0][3]["parameters"]["size"] == "960*1280"
    assert result.provider_task_id == "qwen-original" and result.status == "submitted"
    state["body"] = {
        "output": {
            "task_id": "qwen-original",
            "task_status": "SUCCEEDED",
            "choices": [{"message": {"content": [{"image": base + "/result.png"}]}}],
        }
    }
    result = gateway.poll(settings, "qwen-original", "fixture-secret", "dashscope_images.v1")
    assert calls[1][0:2] == ("GET", "/api/v1/tasks/qwen-original")
    assert result.status == "succeeded" and result.outputs[0]["url"] == base + "/result.png"


@pytest.mark.parametrize(
    "adapter,model,count",
    [
        ("ark_images.v1", "doubao-seedream-5-0", 1),
        ("ark_images.v1", "doubao-seedream-5-0", 2),
        ("dashscope_images.v1", "qwen-image-3.0-pro", 2),
    ],
)
def test_canvas_saved_private_references_are_inline_and_preserve_order(
    gateway, provider, adapter, model, count
):
    base, state, calls = provider
    state["body"] = (
        {"data": [{"b64_json": "aW1hZ2U="}]}
        if adapter == "ark_images.v1"
        else {"output": {"task_id": "qwen-original", "task_status": "PENDING"}}
    )
    payload = frozen_request(
        request("image", references=[{"storageKey": f"resource:{12 + i}"} for i in range(count)]),
        adapter,
    )
    payload["input"]["reference_urls"] = [
        f"http://127.0.0.1:9000/private-{i}?signature=opaque" for i in range(count)
    ]
    before = deepcopy(payload)
    images = [reference_image(), reference_image("JPEG", "blue")][:count]
    loaded = []

    def load_reference(index, limit, deadline):
        assert limit == 30 * 1024**2 and deadline > 0
        loaded.append(index)
        return images[index]

    gateway.submit(
        snapshot(base, "image", model),
        payload,
        "fixture-secret",
        adapter,
        reference_loader=load_reference,
    )
    assert loaded == list(range(count)) and len(calls) == 1 and payload == before
    if adapter == "ark_images.v1":
        value = calls[0][3]["image"]
        values = [value] if isinstance(value, str) else value
    else:
        content = calls[0][3]["input"]["messages"][0]["content"]
        values = [part["image"] for part in content[:-1]]
        assert content[-1] == {"text": "原版提示词"}
    assert values == [
        f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        for mime, data in zip(["image/png", "image/jpeg"], images, strict=False)
    ]
    assert "127.0.0.1:9000" not in str(calls[0][3])


@pytest.mark.parametrize("missing_loader", [True, False])
def test_canvas_private_reference_preparation_failure_never_submits(
    gateway, provider, missing_loader
):
    base, _, calls = provider
    payload = frozen_request(
        request("image", references=[{"storageKey": "resource:12"}]), "ark_images.v1"
    )
    payload["input"]["reference_urls"] = [base + "/must-not-fetch"]
    options = {} if missing_loader else {"reference_loader": lambda *_: b"not an image"}
    with pytest.raises(GenerationError):
        gateway.submit(
            snapshot(base, "image", "doubao-seedream-5-0"),
            payload,
            "fixture-secret",
            "ark_images.v1",
            **options,
        )
    assert not calls


@pytest.mark.parametrize(
    "adapter,model",
    [
        ("ark_images.v1", "doubao-seedream-5-0"),
        ("dashscope_images.v1", "qwen-image-3.0-pro"),
        ("openai_images.v1", "gpt-image-2"),
    ],
)
def test_canvas_worker_inline_path_does_not_need_public_media_presigning(adapter, model):
    from short_drama.service.generation_execution_service import GenerationExecutionService

    payload = frozen_request(
        request("image", references=[{"storageKey": "resource:12"}, {"storageKey": "resource:13"}]),
        adapter,
    )
    before = deepcopy(payload)

    @contextmanager
    def factory():
        yield SimpleNamespace(get=lambda *_: pytest.fail("内联路径不能依赖签名 URL 查询"))

    worker = GenerationExecutionService.__new__(GenerationExecutionService)
    worker.factory = factory
    worker.storage = SimpleNamespace(presigned_get=lambda *_: pytest.fail("不能签名私人参考 URL"))
    worker.settings = SimpleNamespace(
        minio_image_bucket="images", minio_video_bucket="videos", minio_audio_bucket="audio"
    )
    hydrated = worker._input(
        SimpleNamespace(
            request_data=payload,
            config_snapshot=snapshot("https://example.test", "image", model),
            adapter=adapter,
        )
    )
    assert hydrated["input"]["reference_urls"] == [
        "https://reference.invalid/0",
        "https://reference.invalid/1",
    ]
    assert payload == before


def test_standard_ark_keeps_original_url_wire_and_does_not_read_inline_bytes(gateway, provider):
    base, state, calls = provider
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}]}
    payload = {"input": {"prompt": "标准参考", "reference_urls": [base + "/reference"]}}
    result = gateway.submit(
        snapshot(base, "image", "doubao-seedream-5-0"),
        payload,
        "fixture-secret",
        "ark_images.v1",
        reference_loader=lambda *_: pytest.fail("标准模式不能进入内联分支"),
    )
    assert result.status == "succeeded" and calls[0][3]["image"] == [base + "/reference"]


@pytest.mark.parametrize("limit", ["per_image", "total"])
def test_canvas_inline_byte_limits_fail_before_any_provider_http(
    gateway, provider, monkeypatch, limit
):
    from short_drama.ai import canvas_image_references

    base, _, calls = provider
    png = reference_image()
    monkeypatch.setattr(
        canvas_image_references,
        "CANVAS_REFERENCE_IMAGE_BYTES" if limit == "per_image" else "CANVAS_REFERENCE_TOTAL_BYTES",
        len(png) - 1 if limit == "per_image" else 2 * len(png) - 1,
    )
    count = 1 if limit == "per_image" else 2
    payload = frozen_request(
        request("image", references=[{"storageKey": f"resource:{12 + i}"} for i in range(count)]),
        "ark_images.v1",
    )
    payload["input"]["reference_urls"] = [base + f"/must-not-fetch-{i}" for i in range(count)]
    with pytest.raises(GenerationError, match="reference_images_too_large") as error:
        gateway.submit(
            snapshot(base, "image", "doubao-seedream-5-0"),
            payload,
            "fixture-secret",
            "ark_images.v1",
            reference_loader=lambda *_: png,
        )
    assert not error.value.accepted_unknown and not calls


@pytest.mark.parametrize("valid_image", [True, False])
def test_inline_storage_stream_closes_before_submission_or_validation_failure(
    gateway, provider, references, valid_image
):
    from short_drama.service.generation_references import StoredImageReferences

    base, state, calls = provider
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}]}
    factory, storage, settings, media = references
    image = reference_image() if valid_image else b"invalid image"
    media.size, media.chunks = len(image), [image[:4], image[4:]]
    loader = StoredImageReferences(factory, storage, settings, ["1"])
    payload = frozen_request(
        request("image", references=[{"storageKey": "resource:1"}]), "ark_images.v1"
    )
    payload["input"]["reference_urls"] = ["https://reference.invalid/0"]
    if valid_image:
        result = gateway.submit(
            snapshot(base, "image", "doubao-seedream-5-0"),
            payload,
            "fixture-secret",
            "ark_images.v1",
            reference_loader=loader,
        )
        assert len(calls) == 1 and "image" not in result.resolved_parameters
        assert base64.b64encode(image).decode("ascii") not in str(result.resolved_parameters)
    else:
        with pytest.raises(GenerationError, match="invalid_reference_image"):
            gateway.submit(
                snapshot(base, "image", "doubao-seedream-5-0"),
                payload,
                "fixture-secret",
                "ark_images.v1",
                reference_loader=loader,
            )
        assert not calls
    assert media.closed
