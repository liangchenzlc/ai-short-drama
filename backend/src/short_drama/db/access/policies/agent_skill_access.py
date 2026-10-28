"""Skill package versions are append-only, including in worker sessions."""

from sqlalchemy import inspect

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain.skills.catalog.skill import AgentSkillSetting, AgentSkillVersion


def guard_skill_bulk(state) -> None:
    table = getattr(state.statement, "table", None)
    if (state.is_update or state.is_delete) and getattr(
        table, "name", None
    ) == "agent_skill_versions":
        raise WorkflowError("agent_skill_version_immutable", "Skill 内容版本不可改写或删除", 403)


def guard_skill_versions(session) -> None:
    for entity in list(session.dirty) + list(session.deleted):
        if isinstance(entity, AgentSkillVersion) and (
            entity in session.deleted or session.is_modified(entity, include_collections=False)
        ):
            raise WorkflowError(
                "agent_skill_version_immutable", "Skill 内容版本不可改写或删除", 403
            )
    actor = session.info.get("actor")
    if actor is None:
        return
    for entity in list(session.new) + list(session.dirty) + list(session.deleted):
        if not isinstance(entity, (AgentSkillVersion, AgentSkillSetting)):
            continue
        if entity.owner_user_id != actor.user_id:
            raise NotFound("Skill 不存在或无权访问")
        if entity not in session.new and any(
            inspect(entity).attrs[field].history.has_changes()
            for field in ("owner_user_id", "skill_key", "skill_id")
        ):
            raise WorkflowError("ownership_immutable", "Skill 设置身份不能更改", 403)
        if entity.skill_id is not None:
            from short_drama.db.scope.resource_scope import _agent_parent

            parent = _agent_parent(session, "agent_skills", entity.skill_id)
            if parent.owner_user_id != actor.user_id or entity.skill_key != str(parent.id):
                raise NotFound("Skill 版本或设置不属于本人")
        elif isinstance(entity, AgentSkillSetting):
            from short_drama.core.skill_catalog import BUILTIN_NAMES

            if entity.skill_key not in BUILTIN_NAMES:
                raise NotFound("内置 Skill 不存在")
