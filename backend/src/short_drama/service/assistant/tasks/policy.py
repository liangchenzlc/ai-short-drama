"""Read-only projection of current assistant capabilities for Agent tools."""

from short_drama.dao.ai_config.management.models import AIModelConfigDAO
from short_drama.dao.assistant.settings.preferences import AssistantSettingsDAO
from short_drama.schemas.assistant.tasks.policy import GenerationPolicyRead

MEDIA_KINDS = ("image", "video", "audio")


def policy_state_locked(session, owner: int, *, lock=False) -> tuple[bool, int]:
    row = AssistantSettingsDAO(session).preference(owner, lock=lock)
    return (
        (bool(row.workbench_enabled and row.media_generation_enabled), row.settings_version)
        if row
        else (True, 0)
    )


def generation_policy_locked(session, owner: int) -> GenerationPolicyRead:
    row = AssistantSettingsDAO(session).preference(owner)
    workbench = bool(row.workbench_enabled) if row else True
    enabled = workbench and (bool(row.media_generation_enabled) if row else True)
    configured = AIModelConfigDAO(session).enabled_service_types(owner)
    return GenerationPolicyRead(
        workbench_enabled=workbench,
        long_term_memory_enabled=bool(row.long_term_memory_enabled) if row else True,
        media_generation_enabled=enabled,
        settings_version=row.settings_version if row else 0,
        configured_media=[kind for kind in MEDIA_KINDS if kind in configured],
        available_media=[kind for kind in MEDIA_KINDS if kind in configured] if enabled else [],
    )
