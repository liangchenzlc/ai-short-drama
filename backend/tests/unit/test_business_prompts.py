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


def test_prompt_loader_fails_explicitly_for_missing_template():
    with pytest.raises(RuntimeError, match="Prompt template not found"):
        load_prompt("missing/template.md")
