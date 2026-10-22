"""首次创建绘图清单的身份、输入错误和旧请求摘要。"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.core.exceptions import NotFound
from short_drama.db import access
from short_drama.schemas.canvas import CanvasSummaryRead
from short_drama.schemas.canvas_creation import CanvasCreationRequest, canvas_creation_payload
from short_drama.service.base import BaseService
from short_drama.service.canvas_document import content_hash


def test_optional_drawings_preserve_existing_creation_digest_input():
    expected = {"title": "原请求", "source_key": "canvas", "source_document": None}
    for extra in ({}, {"drawing_documents": []}):
        upgraded = CanvasCreationRequest.model_validate({**expected, **extra})
        assert canvas_creation_payload(upgraded) == expected
        assert content_hash(canvas_creation_payload(upgraded)) == content_hash(expected)


@pytest.mark.parametrize(
    "metadata",
    [{"drawingId": []}, {"drawingId": "drawing", "drawingRevision": {"value": "1"}}],
)
def test_invalid_drawing_metadata_is_a_validation_error(metadata):
    with pytest.raises(ValidationError):
        CanvasCreationRequest.model_validate(
            {
                "source_document": {
                    "id": "canvas",
                    "title": "绘图",
                    "nodes": [
                        {
                            "id": "node",
                            "type": "drawing",
                            "title": "绘图",
                            "position": {"x": 0, "y": 0},
                            "width": 360,
                            "height": 240,
                            "metadata": metadata,
                        }
                    ],
                }
            }
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"source_canvas_key": "parent"},
        {"source_canvas_row_version": "1"},
        {"source_canvas_key": "parent", "source_canvas_row_version": 1},
        {"source_canvas_key": "parent", "source_canvas_row_version": "0"},
        {"source_canvas_key": "parent", "source_canvas_row_version": "01"},
        {"source_canvas_key": "parent", "source_canvas_row_version": "18446744073709551616"},
        {"source_canvas_key": "bad/key", "source_canvas_row_version": "1"},
        {"source_project_id": "1"},
    ],
)
def test_creation_origin_rejects_incomplete_invalid_and_direct_binding(extra):
    with pytest.raises(ValidationError):
        CanvasCreationRequest.model_validate(extra)


def test_creation_origin_affects_digest_and_keeps_large_decimal_version():
    original = {"title": "原请求", "source_key": "canvas", "source_document": None}
    request = CanvasCreationRequest.model_validate(
        {**original, "source_canvas_key": "parent", "source_canvas_row_version": "9007199254740999"}
    )
    payload = canvas_creation_payload(request)
    assert payload["source_canvas_row_version"] == "9007199254740999"
    assert content_hash(payload) != content_hash(original)
    assert "$source_project_binding" not in payload
    empty = CanvasCreationRequest.model_validate(
        {**original, "source_canvas_key": None, "source_canvas_row_version": None}
    )
    assert canvas_creation_payload(empty) == original


def test_old_summary_accepts_missing_readonly_binding():
    summary = CanvasSummaryRead.model_validate(
        {
            "id": "1",
            "project_id": "2",
            "source_key": "canvas",
            "title": "画布",
            "row_version": "1",
            "schema_version": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    assert "source_project_id" not in summary.model_dump()


@pytest.mark.parametrize("defer", [False, True])
def test_transaction_defaults_lock_request_project_and_defer_only_lock(monkeypatch, defer):
    observed = []
    session = SimpleNamespace(
        info={"request_project": 2},
        in_transaction=lambda: False,
        begin=lambda: nullcontext(),
    )
    service = object.__new__(BaseService)
    service.session = session
    monkeypatch.setattr(
        access, "require_project", lambda *args, **kwargs: observed.append(kwargs["lock"])
    )
    options = {"lock_request_project": False} if defer else {}
    with service._transaction(**options):
        assert observed == [not defer]
        assert session.info == {"request_project": 2}


@pytest.mark.parametrize("lock_request_project", [False, True])
def test_transaction_defer_cannot_bypass_current_project_authorization(
    monkeypatch, lock_request_project
):
    session = SimpleNamespace(
        info={"request_project": 2},
        in_transaction=lambda: False,
        begin=lambda: nullcontext(),
    )
    service = object.__new__(BaseService)
    service.session = session

    def deny(*args, **kwargs):
        raise NotFound("Denied current request project")

    monkeypatch.setattr(access, "require_project", deny)
    with pytest.raises(NotFound):
        with service._transaction(lock_request_project=lock_request_project):
            pytest.fail("Authorization failure must not reach operation")
    assert session.info == {"request_project": 2}
