import pytest

from short_drama.ai.prompts import load_prompt, system_prompt
from short_drama.ai.prompts.registry import asset_image_system_prompt, shot_image_system_prompt


def test_asset_prompt_composes_only_requested_category_rules():
    prompt = system_prompt("script_assets", kinds=["prop"])

    assert "删除测试" in prompt
    assert "桌椅、花瓶" in prompt
    assert "角色规则" not in prompt
    assert "场景规则" not in prompt


def test_asset_prompt_rejects_unknown_category():
    with pytest.raises(ValueError, match="Unsupported asset kind"):
        system_prompt("script_assets", kinds=["costume"])


def test_storyboard_prompt_defines_beat_segmentation_and_duration_target():
    prompt = system_prompt("script_shots")

    assert "一句话一个镜头" in prompt
    assert "平均镜头时长" in prompt
    assert "source_excerpt" in prompt


def test_novel_prompt_does_not_request_json_for_plain_text_script():
    prompt = system_prompt("novel_script")

    assert "只返回剧本正文" in prompt
    assert "一个 JSON 对象" not in prompt
    assert "Schema" not in prompt


def test_storyboard_prompt_describes_the_full_parser_contract():
    prompt = system_prompt("script_shots")

    for field in (
        "shots",
        "title",
        "source_excerpt",
        "story_beat",
        "script",
        "duration_ms",
        "asset_ids",
    ):
        assert field in prompt
    assert "1000" in prompt and "10000" in prompt
    assert "source.assets" in prompt
    assert "逐字" in prompt


def test_asset_prompt_describes_the_full_parser_contract():
    prompt = system_prompt("script_assets", kinds=["scene"])

    for field in (
        "schema_version",
        "items",
        "kind",
        "name",
        "description",
        "prompt",
        "importance",
        "story_function",
        "aliases",
        "tags",
        "label",
        "scene_time",
    ):
        assert field in prompt
    assert "source.max_candidates" in prompt
    assert "不要输出 `model_id`" in prompt


def test_prompt_loader_fails_explicitly_for_missing_template():
    with pytest.raises(RuntimeError, match="Prompt template not found"):
        load_prompt("missing/template.md")


@pytest.mark.parametrize("kind", ["character", "prop", "scene"])
def test_asset_extraction_and_generation_share_only_the_selected_view_contract(kind):
    extraction = system_prompt("script_assets", kinds=[kind])
    generation = asset_image_system_prompt(kind)
    view = load_prompt(f"asset_views/{kind}.md")
    assert view in extraction and view in generation
    for other in {"character", "prop", "scene"} - {kind}:
        assert load_prompt(f"asset_views/{other}.md") not in extraction
        assert load_prompt(f"asset_views/{other}.md") not in generation
    assert "剧本没有写明的普通视觉细节由你直接补全" in extraction
    assert "不得补写原文没有支持的身份、年龄、外貌、材质、地点或情节" not in extraction


@pytest.mark.parametrize("layout", ["single", "four", "five", "nine"])
def test_shot_image_selects_one_layout_without_mixing_start_frame_and_action_sequence(layout):
    prompt = shot_image_system_prompt(layout)
    assert f"## 本次布局：{layout}" in prompt
    assert ("## 多宫格动作规则" in prompt) is (layout != "single")
    if layout != "single":
        assert load_prompt("shot_image/single.md") not in prompt
    else:
        assert "第一格对应起点，最后一格对应终点" not in prompt


def test_invalid_image_modes_cannot_resolve_arbitrary_prompt_resources():
    with pytest.raises(ValueError, match="Unsupported asset kind"):
        asset_image_system_prompt("../scene")
    with pytest.raises(ValueError, match="Unsupported shot image layout"):
        shot_image_system_prompt("three")
