import pytest

from short_drama.ai.prompts import load_prompt, system_prompt


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
        "evidence",
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
