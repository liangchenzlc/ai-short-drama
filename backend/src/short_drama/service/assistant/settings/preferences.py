"""Settings CAS and immutable per-Run snapshots; media permission remains live."""

from copy import deepcopy

from short_drama.ai.prompts.registry import (
    PROMPT_VERSIONS,
    assistant_prompt,
    assistant_tool_selection_prompt,
)
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.core.identity import require_actor
from short_drama.dao.assistant.settings.preferences import AssistantSettingsDAO
from short_drama.domain.assistant.operations.preference import AgentPreference
from short_drama.schemas.assistant.settings.preferences import (
    AssistantRuntimeLimits,
    AssistantSettingsPatch,
    AssistantSettingsRead,
)
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.assistant.memory.memory_store import invalidate_memory_runs
from short_drama.service.base import BaseService, utcnow
from short_drama.utils.snowflake import next_id

DEFAULT_INSTRUCTIONS = (
    "理解用户目标，按需使用方法与工具，以实际保存并读回的成果交付；普通讨论直接回答。"
)


def deployment_limits(settings) -> AssistantRuntimeLimits:
    return AssistantRuntimeLimits(
        model_call_limit=settings.agent_run_decision_cap,
        output_token_limit=settings.agent_run_output_token_cap,
        subagent_max_concurrency=3,
    )


def settings_defaults(settings) -> AssistantSettingsRead:
    return AssistantSettingsRead(
        custom_instructions=None,
        default_instructions=DEFAULT_INSTRUCTIONS,
        workbench_enabled=True,
        media_generation_enabled=True,
        long_term_memory_enabled=True,
        settings_version=0,
    )


def settings_locked(session, owner: int, settings) -> AssistantSettingsRead:
    result = settings_defaults(settings)
    row = AssistantSettingsDAO(session).preference(owner)
    if row is None:
        return result
    values = {
        key: getattr(row, key)
        for key in (
            "custom_instructions",
            "workbench_enabled",
            "media_generation_enabled",
            "long_term_memory_enabled",
            "settings_version",
        )
    }
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
        **deployment_limits(settings).model_dump(),
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
        with self._transaction():
            # 与运行时保持 Run → preference 的锁顺序，切换后撤去下一次请求的旧记忆。
            if "long_term_memory_enabled" in values.model_fields_set:
                invalidate_memory_runs(self.session, owner)
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
                    id=next_id(),
                    owner_user_id=owner,
                    media_generation_enabled=1,
                    workbench_enabled=1,
                    long_term_memory_enabled=1,
                    memory_generation=0,
                )
                self.session.add(row)
            changes = values.model_dump(exclude={"settings_version"}, exclude_unset=True)
            if (
                "long_term_memory_enabled" in changes
                and bool(row.long_term_memory_enabled) != changes["long_term_memory_enabled"]
            ):
                row.memory_generation += 1
            for key, value in changes.items():
                setattr(row, key, value)
            row.settings_version, row.updated_at = version + 1, utcnow()
            self.session.flush()
            return settings_locked(self.session, owner, self.settings)
