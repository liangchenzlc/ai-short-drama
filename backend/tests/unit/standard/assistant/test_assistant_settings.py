"""Public preferences are partial; execution budgets remain deployment concerns."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError


def test_settings_patch_only_contains_changed_fields():
    from short_drama.schemas.assistant.settings.preferences import AssistantSettingsPatch

    patch = AssistantSettingsPatch.model_validate(
        {"settings_version": "9007199254740993", "workbench_enabled": False}
    )
    assert patch.model_dump(mode="json", exclude_unset=True) == {
        "settings_version": "9007199254740993",
        "workbench_enabled": False,
    }


@pytest.mark.parametrize(
    "field", ["workbench_enabled", "media_generation_enabled", "long_term_memory_enabled"]
)
def test_switches_cannot_be_null(field):
    from short_drama.schemas.assistant.settings.preferences import AssistantSettingsPatch

    with pytest.raises(ValidationError):
        AssistantSettingsPatch.model_validate({"settings_version": "0", field: None})


def test_settings_patch_requires_an_edit_and_rejects_user_budgets():
    from short_drama.schemas.assistant.settings.preferences import AssistantSettingsPatch

    for extra in ({}, {"model_call_limit": 240}, {"default_media_limits": {}}):
        with pytest.raises(ValidationError):
            AssistantSettingsPatch.model_validate({"settings_version": "0", **extra})


def test_blank_instructions_use_defaults():
    from short_drama.schemas.assistant.settings.preferences import AssistantSettingsPatch

    patch = AssistantSettingsPatch.model_validate(
        {"settings_version": "0", "custom_instructions": "  "}
    )
    assert patch.custom_instructions is None


def test_public_defaults_do_not_expose_execution_budgets():
    from short_drama.service.assistant.settings.preferences import (
        deployment_limits,
        settings_defaults,
    )

    result = settings_defaults(SimpleNamespace())
    assert result.workbench_enabled and result.media_generation_enabled
    assert result.long_term_memory_enabled
    assert "model_call_limit" not in result.model_dump()
    assert deployment_limits(SimpleNamespace()).model_dump() == {"subagent_max_concurrency": 3}
    assert result.settings_version == 0
    assert result.custom_instructions is None
    assert result.default_instructions
