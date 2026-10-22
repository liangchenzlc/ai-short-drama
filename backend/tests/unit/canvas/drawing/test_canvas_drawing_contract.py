"""绘图结构边界保留 Excalidraw 内容，拒绝不能持久化的输入。"""

import pytest
from pydantic import ValidationError

from short_drama.schemas.canvas_drawing import CanvasDrawingWrite


def test_drawing_contract_keeps_content_and_uint64_revision():
    snapshot = {
        "elements": [{"id": "text", "text": "apiKey 是画板上的文字", "x": 10.399993896484375}],
        "files": {"image": {"id": "image", "dataURL": "data:image/png;base64,aW1hZ2U="}},
    }
    value = CanvasDrawingWrite.model_validate(
        {
            "drawingId": "drawing",
            "revision": "9007199254740993",
            "snapshot": snapshot,
            "pageCount": 4,
            "previewResourceId": "",
            "render": {"resourceId": ""},
        }
    )
    serialized = value.model_dump(mode="json", by_alias=True)
    assert serialized["revision"] == "9007199254740993"
    assert serialized["snapshot"] == snapshot
    assert serialized["pageCount"] == 1
    assert serialized["previewResourceId"] is None
    assert serialized["render"]["resourceId"] is None


@pytest.mark.parametrize(
    "snapshot",
    [
        {"elements": [{"api_key": "test-value"}]},
        {"elements": [{"x": float("inf")}]},
        {"files": {"image": {"dataURL": "blob:only-this-window"}}},
    ],
)
def test_drawing_contract_rejects_credentials_nonfinite_and_transient_files(snapshot):
    with pytest.raises(ValidationError):
        CanvasDrawingWrite.model_validate(
            {
                "drawingId": "drawing",
                "revision": "0",
                "snapshot": snapshot,
            }
        )
