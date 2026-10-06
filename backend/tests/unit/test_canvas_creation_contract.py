"""首次创建绘图清单的身份、输入错误和旧请求摘要。"""

import pytest
from pydantic import ValidationError

from short_drama.schemas.canvas import CanvasCreateRequest
from short_drama.schemas.canvas_creation import CanvasCreationRequest, canvas_creation_payload


def test_optional_drawings_preserve_existing_creation_digest_input():
    original = CanvasCreateRequest(title="原请求", source_key="canvas")
    expected = original.model_dump(mode="json")
    for extra in ({}, {"drawing_documents": []}):
        upgraded = CanvasCreationRequest.model_validate({**expected, **extra})
        assert canvas_creation_payload(upgraded) == expected


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
