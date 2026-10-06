"""画布内联引用的授权入口、内存预算与截止时间边界。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_canvas_generation_parameters import frozen_request, request, snapshot
from test_generation_adapters import reference_image

from short_drama.ai import GenerationError
from short_drama.ai import canvas_image_references as references
from short_drama.ai.adapters import build_submission


def prepared(count=1):
    payload = frozen_request(
        request("image", references=[{"storageKey": f"resource:{12 + i}"} for i in range(count)]),
        "ark_images.v1",
    )
    payload["input"]["reference_urls"] = [f"https://reference.invalid/{i}" for i in range(count)]
    settings = snapshot("image", "doubao-seedream-5-0")
    _, _, body = build_submission(settings, payload, "ark_images.v1")
    return settings, payload, body


def test_canvas_limit_defaults_record_source_per_image_and_host_total_budget():
    assert references.CANVAS_REFERENCE_IMAGE_BYTES == 30 * 1024**2
    assert references.CANVAS_REFERENCE_MAX_COUNT == 16
    assert references.CANVAS_REFERENCE_TOTAL_BYTES == 100 * 1024**2


@pytest.mark.parametrize("field", ["source", "canvas_request", "canvas_parameters"])
def test_inline_reader_requires_all_frozen_canvas_gates(field):
    settings, payload, _ = prepared()
    assert references.uses_canvas_inline_images(settings, payload, "ark_images.v1")
    payload.pop(field)
    assert not references.uses_canvas_inline_images(settings, payload, "ark_images.v1")


def test_wanx_public_url_contract_does_not_enter_inline_reader():
    settings, payload, _ = prepared()
    settings["model_key"] = "wanx-v1"
    assert not references.uses_canvas_inline_images(settings, payload, "dashscope_images.v1")


@pytest.mark.parametrize("identifiers", [[], ["https://private/secret"], [12]])
def test_only_frozen_stable_media_ids_can_trigger_storage_read(identifiers):
    _, payload, body = prepared()
    payload["input"]["reference_media_ids"] = identifiers
    with pytest.raises(GenerationError, match="unresolved_media_reference") as caught:
        references.inline_saved_image_references(
            body, payload, "ark_images.v1", lambda *_: pytest.fail("不能读取任意 URL"), float("inf")
        )
    assert "secret" not in str(caught.value)


def test_expired_budget_never_reads_reference_or_mutates_recipe():
    _, payload, body = prepared()
    before = deepcopy(body)
    with pytest.raises(GenerationError, match="timeout"):
        references.inline_saved_image_references(
            body, payload, "ark_images.v1", lambda *_: pytest.fail("预算已耗尽"), 0
        )
    assert body == before


def test_budget_expiring_during_read_never_replaces_reference_recipe(monkeypatch):
    _, payload, body = prepared()
    before = deepcopy(body)
    ticks = iter([0, 2])
    monkeypatch.setattr(references, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(GenerationError, match="timeout"):
        references.inline_saved_image_references(
            body, payload, "ark_images.v1", lambda *_: reference_image(), 1
        )
    assert body == before


def test_duplicate_references_still_count_toward_source_limit():
    _, payload, body = prepared()
    payload["input"]["reference_media_ids"] = ["12"] * 17
    payload["input"]["reference_urls"] = ["https://reference.invalid/0"] * 17
    with pytest.raises(GenerationError, match="reference_images_too_large"):
        references.inline_saved_image_references(
            body, payload, "ark_images.v1", lambda *_: pytest.fail("超过引用数量限制"), float("inf")
        )
