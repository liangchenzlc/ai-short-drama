"""完整风格合同与媒体引用；不使用数据库或供应商。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.schemas.canvas_style import CanvasStyleSnapshot
from short_drama.service.canvas_playback_service import playback_operation
from short_drama.service.canvas_style_service import style_resources


def snapshot():
    return {
        "schemaVersion": 1,
        "presetId": "test-style",
        "title": "测试画风",
        "description": "完整快照",
        "tags": ["测试"],
        "prompt": "冷色写实",
        "negativePrompt": "模糊",
        "selection": {"medium": "live-action"},
        "source": "builtin",
        "revision": 1,
        "executionPolicy": "strict-assets",
        "assets": [
            {
                "id": "reference",
                "kind": "reference",
                "provider": "manual",
                "title": "参考图",
                "status": "validated",
                "weight": 0.75,
                "referenceResourceIds": ["9007199254740993"],
                "referenceUrls": ["/api/v1/canvas-runtime/resources/9007199254740994/file"],
                "parameters": {"steps": 20, "cfg": 7.5},
                "license": {"commercial": False, "note": "测试用途"},
            }
        ],
        "coverUrl": "/api/v1/canvas-runtime/resources/9007199254740995/file",
    }


def test_full_snapshot_roundtrip_preserves_assets_and_string_identifiers():
    value = snapshot()
    parsed = CanvasStyleSnapshot.model_validate(value)
    assert parsed.model_dump(mode="json", by_alias=True, exclude_none=True) == value
    assert style_resources(parsed) == {9007199254740993, 9007199254740994, 9007199254740995}


@pytest.mark.parametrize(
    "change",
    [
        {"schemaVersion": True},
        {"revision": True},
        {"revision": 0},
        {"prompt": " "},
        {"title": "x" * 81},
        {"unexpected": True},
        {"selection": {"": "bad"}},
    ],
)
def test_invalid_snapshot_is_rejected(change):
    with pytest.raises(ValidationError):
        CanvasStyleSnapshot.model_validate({**snapshot(), **change})


def test_missing_validated_asset_and_duplicate_id_are_rejected():
    value = snapshot()
    value["assets"] *= 2
    with pytest.raises(ValidationError):
        CanvasStyleSnapshot.model_validate(value)
    value = snapshot()
    value["assets"][0].pop("referenceResourceIds")
    value["assets"][0].pop("referenceUrls")
    with pytest.raises(ValidationError):
        CanvasStyleSnapshot.model_validate(value)


def test_playback_deduplication_tracks_source_version_and_preserves_large_id():
    source = SimpleNamespace(
        id=9007199254740993,
        storage_locator="minio://video/original",
        checksum_sha256="a" * 64,
        byte_size=100,
    )
    operation = playback_operation(source)
    assert operation.startswith("playback:9007199254740993:") and len(operation) <= 128
    assert playback_operation(deepcopy(source)) == operation
    source.checksum_sha256 = "b" * 64
    assert playback_operation(source) != operation
