"""验证真实节点契约；不复制服务算法或读取源码作为行为证明。"""

import pytest
from pydantic import ValidationError

from short_drama.schemas.canvas import CanvasNodeDocument


@pytest.mark.parametrize("kind", ["text", "image", "video", "audio"])
def test_source_node_media_and_drafts_use_metadata(kind):
    value = {
        "id": "source-node",
        "type": kind,
        "title": "源节点",
        "position": {"x": 120, "y": 80},
        "width": 320,
        "height": 180,
        "metadata": {"prompt": "本人草稿"},
    }
    parsed = CanvasNodeDocument.model_validate(value)
    assert parsed.type == kind
    assert parsed.metadata == value["metadata"]
    assert parsed.position.model_dump() == value["position"]


def test_top_level_media_content_is_not_a_source_node_field():
    with pytest.raises(ValidationError) as error:
        CanvasNodeDocument.model_validate(
            {
                "id": "unsupported-node",
                "type": "media",
                "title": "错误的媒体结构",
                "position": {"x": 0, "y": 0},
                "width": 320,
                "height": 180,
                "content": {"mediaType": "video"},
            }
        )
    assert any(
        item["loc"] == ("content",) and item["type"] == "extra_forbidden"
        for item in error.value.errors()
    )
