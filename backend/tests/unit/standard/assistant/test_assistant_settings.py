"""Single assistant settings contracts, separate from the live media policy."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError


def test_settings_patch_requires_complete_replacement_and_decimal_version():
    from short_drama.schemas.assistant.settings.preferences import AssistantSettingsPatch

    values = {
        "custom_instructions": None,
        "model_call_limit": 240,
        "output_token_limit": 2_000_000,
        "subagent_max_concurrency": 3,
        "default_media_limits": {"images": 0},
        "settings_version": "0",
    }
    payload = AssistantSettingsPatch.model_validate(values)
    assert payload.default_media_limits.model_dump() == {"images": 0, "videos": None, "audio": None}
    assert payload.model_dump(mode="json")["settings_version"] == "0"
    for key in values:
        with pytest.raises(ValidationError):
            AssistantSettingsPatch.model_validate({k: v for k, v in values.items() if k != key})


@pytest.mark.parametrize(
    ("field", "value"),
    [("model_call_limit", 19), ("output_token_limit", 99_999), ("subagent_max_concurrency", 4)],
)
def test_settings_patch_enforces_public_ranges(field, value):
    from short_drama.schemas.assistant.settings.preferences import AssistantSettingsPatch

    with pytest.raises(ValidationError):
        AssistantSettingsPatch.model_validate(
            {
                "custom_instructions": None,
                "model_call_limit": 240,
                "output_token_limit": 2_000_000,
                "subagent_max_concurrency": 3,
                "default_media_limits": {},
                "settings_version": 0,
                field: value,
            }
        )


def test_recommended_defaults_are_clamped_to_deployment_limits():
    from short_drama.service.assistant.settings.preferences import settings_defaults

    result = settings_defaults(
        SimpleNamespace(agent_run_decision_cap=100, agent_run_output_token_cap=500_000)
    )
    assert result.model_call_limit == 100
    assert result.output_token_limit == 500_000
    assert result.subagent_max_concurrency == 3
    assert result.settings_version == 0
    assert result.custom_instructions is None
    assert result.default_instructions
