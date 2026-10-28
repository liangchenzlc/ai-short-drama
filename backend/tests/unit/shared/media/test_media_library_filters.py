import pytest

from short_drama.service.media_asset_service import MediaAssetService
from tests.support.generation_fixtures import generation_session, settings
from tests.support.media_library_fixtures import seed_media_library


@pytest.mark.parametrize(
    ("mode", "kind", "expected"),
    [
        (None, None, [7, 6, 5, 4, 3, 2, 1]),
        (None, "image", [7, 4, 1]),
        (None, "video", [5, 2]),
        (None, "audio", [6, 3]),
        ("standard", None, [5, 3, 1]),
        ("standard", "image", [1]),
        ("standard", "video", [5]),
        ("standard", "audio", [3]),
        ("infinite_canvas", None, [6, 4, 2]),
        ("infinite_canvas", "image", [4]),
        ("infinite_canvas", "video", [2]),
        ("infinite_canvas", "audio", [6]),
    ],
)
def test_media_library_filters_all_modalities_by_origin_before_counting(mode, kind, expected):
    with generation_session() as session:
        session.info["actor"] = seed_media_library(session)
        result = MediaAssetService(session, settings, None).list(
            0, 24, {"workspace_mode": mode, "media_type": kind}
        )
        assert [int(item["asset_id"]) - 4000 for item in result["items"]] == expected
        assert result["total"] == len(expected)


def test_name_search_and_pagination_share_the_filtered_total_and_stable_order():
    with generation_session() as session:
        session.info["actor"] = seed_media_library(session)
        service = MediaAssetService(session, settings, None)
        result = service.list(1, 2, {"name": "雨夜"})
        assert [item["asset_id"] for item in result["items"]] == ["4004", "4002"]
        assert result["total"] == 4
        result = service.list(0, 24, {"name": "雨夜", "workspace_mode": "standard"})
        assert [item["asset_id"] for item in result["items"]] == ["4001"]
        assert result["total"] == 1
        assert service.list(0, 24, {"name": "不存在"})["total"] == 0
