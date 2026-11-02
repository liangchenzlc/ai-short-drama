"""Settings CAS and immutable per-Run snapshots; media permission remains live."""

from copy import deepcopy

from short_drama.ai.prompts.registry import (
    PROMPT_VERSIONS,
    assistant_prompt,
    assistant_tool_selection_prompt,
)
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.dao.assistant.settings.preferences import AssistantSettingsDAO
from short_drama.domain.assistant.operations.preference import AgentPreference
from short_drama.schemas.assistant.settings.preferences import (
    AssistantRuntimeLimits,
    AssistantSettingsPatch,
    AssistantSettingsRead,
)
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.base import BaseService, utcnow
from short_drama.utils.snowflake import next_id

DEFAULT_INSTRUCTIONS = (
    "理解用户目标，按需使用方法与工具，以实际保存并读回的成果交付；普通讨论直接回答。"
)


def deployment_limits(settings) -> AssistantRuntimeLimits:
    return AssistantRuntimeLimits(
        model_call_limit=getattr(settings, "agent_run_decision_cap", 240),
        output_token_limit=getattr(settings, "agent_run_output_token_cap", 2_000_000),
        subagent_max_concurrency=3,
    )


def settings_defaults(settings) -> AssistantSettingsRead:
    limits = deployment_limits(settings)
    return AssistantSettingsRead(
        custom_instructions=None,
        default_instructions=DEFAULT_INSTRUCTIONS,
        model_call_limit=min(240, limits.model_call_limit),
        output_token_limit=min(2_000_000, limits.output_token_limit),
        subagent_max_concurrency=3,
        default_media_limits={},
        settings_version=0,
        deployment_limits=limits,
    )


def settings_locked(session, owner: int, settings) -> AssistantSettingsRead:
    result = settings_defaults(settings)
    row = AssistantSettingsDAO(session).preference(owner)
    if row is None or row.settings_version == 0:
        return result
    values = {
        key: getattr(row, key)
        for key in (
            "custom_instructions",
            "model_call_limit",
            "output_token_limit",
            "subagent_max_concurrency",
            "default_media_limits",
            "settings_version",
        )
    }
    for key, cap in result.deployment_limits.model_dump().items():
        values[key] = min(values[key], cap)
    return AssistantSettingsRead.model_validate({**result.model_dump(), **values})


def freeze_run_settings_locked(session, owner: int, settings, *, tool_catalog_version: int) -> dict:
    current = settings_locked(session, owner, settings)
    instructions = (
        current.custom_instructions
        if current.custom_instructions is not None
        else current.default_instructions
    )
    return {
        "settings_version": str(current.settings_version),
        "instructions": instructions,
        "system_prompt": assistant_prompt(instructions),
        "tool_selector_prompt": assistant_tool_selection_prompt(),
        "prompt_versions": deepcopy(PROMPT_VERSIONS),
        "tool_catalog_version": str(tool_catalog_version),
        **{
            key: getattr(current, key)
            for key in ("model_call_limit", "output_token_limit", "subagent_max_concurrency")
        },
    }


class AssistantSettingsService(BaseService):
    model = AgentPreference

    def __init__(self, session, settings):
        super().__init__(session)
        self.settings = settings
        self.preferences = AssistantSettingsDAO(session)

    def get_settings(self) -> AssistantSettingsRead:
        owner = require_actor(self.session).user_id
        with self._transaction(read_only=True):
            return settings_locked(self.session, owner, self.settings)

    def update_settings(self, payload) -> AssistantSettingsRead:
        values = AssistantSettingsPatch.model_validate(payload)
        owner = require_actor(self.session).user_id
        limits = deployment_limits(self.settings)
        for key, cap in limits.model_dump().items():
            if getattr(values, key) > cap:
                raise WorkflowError(
                    "assistant_settings_deployment_limit",
                    f"{key} 超出当前部署上限 {cap}",
                    422,
                    {"field": key, "limit": cap},
                )
        with self._transaction():
            if self.preferences.lock_owner(owner) is None:
                raise NotFound("账号不存在或已停止使用")
            row = self.preferences.preference(owner, lock=True)
            version = row.settings_version if row else 0
            if values.settings_version != version:
                raise Conflict("助手设置已变化，请保留草稿并重新读取后保存")
            if version == UINT64_MAX:
                raise Conflict("助手设置版本已达到上限")
            if row is None:
                row = AgentPreference(
                    id=next_id(), owner_user_id=owner, media_generation_enabled=1, policy_version=0
                )
                self.session.add(row)
            for key, value in values.model_dump(exclude={"settings_version"}).items():
                setattr(row, key, value)
            row.settings_version, row.updated_at = version + 1, utcnow()
            self.session.flush()
            return settings_locked(self.session, owner, self.settings)
