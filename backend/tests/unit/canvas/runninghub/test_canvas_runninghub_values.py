"""固定 BeefTV 4ca2a65 的字段与尺寸合同；不调用收费供应商。"""

import pytest

from short_drama.ai.adapters.runninghub.values import (
    aspect_ratio,
    dimensions,
    numeric_value,
    ratio_value,
    selected_value,
)
from short_drama.schemas.canvas.generation.runninghub.runninghub import RunningHubField


def test_legacy_fields_recover_nested_ranges_and_string_flags():
    field = RunningHubField.model_validate(
        {
            "node": 12,
            "input": "seed",
            "type": "NUMBER",
            "default": 7,
            "values": [{"range": [{"min_value": "3", "maxValue": 9, "step_value": 2}]}],
            "random_enabled": "true",
            "source_index": "2",
            "bind_prompt": "false",
        }
    )
    assert (field.node_id, field.field_name, field.field_value) == ("12", "seed", 7)
    assert (field.min, field.max, field.step) == ("3", 9, 2)
    assert field.random_enabled is True and field.bind_prompt is False
    assert field.source_index == 2


@pytest.mark.parametrize("container", [{"range": 7}, {"range": ["invalid"]}, {}])
def test_legacy_range_container_does_not_raise_an_unhandled_exception(container):
    field = RunningHubField.model_validate({"node": "2", "input": "steps", "options": container})
    assert field.options == [] and field.min is None


def test_range_bound_with_resolution_suffix_is_not_a_numeric_bound():
    field = RunningHubField(fieldType="NUMBER", min="720p", fieldValue=480)
    assert numeric_value(field, "480p") == 480


@pytest.mark.parametrize(
    "mode,size,quality,expected",
    [
        ("image", "4:3", "", (1360, 1024)),
        ("image", "21:9", "", (2352, 1008)),
        ("image", "3:2-2k", "", (2048, 1360)),
        ("image", "2:3-4k", "", (2560, 3840)),
        ("video", "16:9", "720", (1280, 720)),
        ("video", "9:16", "1080P", (1080, 1920)),
        ("video", "1024x576", "4k", (1024, 576)),
        ("video", "auto", "720", None),
    ],
)
def test_source_dimensions(mode, size, quality, expected):
    assert dimensions(mode, size, quality) == expected


def test_resolution_selector_sends_the_full_node_enum():
    field = RunningHubField(classType="ResolutionSelector", fieldName="aspect_ratio")
    assert ratio_value(field, "9:16") == "9:16 (Portrait Widescreen)"
    assert aspect_ratio("9:16 (Portrait Widescreen)") == "9:16"


def test_video_resolution_and_duration_preserve_declared_options_and_fallback():
    field = RunningHubField(options=[{"value": "480p"}, {"id": "720p"}], fieldValue="480p")
    assert selected_value(field, "720") == "720p"
    assert selected_value(field, "4k") == "480p"
    seconds = RunningHubField(options=["5s", "10秒"], fieldValue="5s")
    assert selected_value(seconds, "10", duration=True) == "10秒"
    assert selected_value(seconds, "20", duration=True) == "5s"


def test_numeric_mapping_uses_go_round_away_from_zero():
    field = RunningHubField(fieldType="NUMBER", min=1, max=10, step=2)
    assert numeric_value(field, "2") == 3
    assert numeric_value(field, "-100") == 1


def test_source_presence_survives_public_freezing():
    absent = RunningHubField(nodeId="3", fieldName="width", fieldType="NUMBER")
    explicit = RunningHubField(nodeId="3", fieldName="width", source="")
    assert "source" not in absent.model_dump(by_alias=True, exclude_none=True)
    assert explicit.model_dump(by_alias=True, exclude_none=True)["source"] == ""
