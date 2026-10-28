from __future__ import annotations

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.core.skill_catalog import BUILTIN_NAMES as BUILTIN_NAMES
from short_drama.domain.assistant.context.context import AgentSkill
from short_drama.domain.skills.catalog.skill import AgentSkillSetting
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.schemas.skills.catalog.skill import resource_path
from short_drama.service.base import BaseService, utcnow
from short_drama.service.skills.catalog.versions import (
    builtin_skills,
    current_version,
    read_skill,
    skill_issues,
    skill_setting,
)
from short_drama.service.skills.packages.skill_package import (
    local_reference_issues,
    mode_tool_issues,
)
from short_drama.utils.snowflake import next_id


class SkillServiceBase(BaseService):
    model = AgentSkill

    def __init__(self, session, settings, storage=None):
        super().__init__(session)
        self.settings = settings
        self.storage = storage

    def _actor(self):
        actor = require_actor(self.session)
        if not self.settings.agent_enabled:
            raise WorkflowError("agent_disabled", "Agent 模式尚未启用", 503)
        return actor

    def _skill(self, identifier, *, lock=False):
        owner = self._actor().user_id
        try:
            identifier = parse_identifier(identifier)
        except ValueError:
            raise NotFound("Skill does not exist") from None
        query = select(AgentSkill).where(
            AgentSkill.id == identifier,
            AgentSkill.owner_user_id == owner,
            AgentSkill.deleted_at.is_(None),
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(query)
        if row is None:
            raise NotFound("Skill does not exist")
        return row

    def _detail(self, row):
        return read_skill(
            row,
            current_version(self.session, str(row.id), row.content_version),
            skill_setting(self.session, row.owner_user_id, str(row.id)),
        )

    def _builtin(self, identifier, owner):
        skill = next(
            (item for item in builtin_skills(self.session) if item.id == str(identifier)), None
        )
        if skill is None:
            raise NotFound("Skill does not exist")
        setting = skill_setting(self.session, owner, skill.id)
        if setting:
            skill = skill.model_copy(
                update={
                    "enabled": bool(setting.enabled),
                    "settings_version": setting.row_version,
                }
            )
        return skill

    def _metadata(self, content):
        metadata = content.model_dump(mode="json", exclude={"instructions", "resources", "enabled"})
        manifest = [{"path": item.path, "kind": item.kind} for item in content.resources]
        metadata["issues"] = list(
            dict.fromkeys(
                skill_issues(metadata, content.instructions, manifest)
                + local_reference_issues(content.instructions, content.resources)
                + mode_tool_issues(content.required_tools, content.modes)
            )
        )
        return metadata

    @staticmethod
    def _validate_write(metadata: dict, resources: list[dict]) -> None:
        for resource in resources:
            try:
                resource_path(resource["path"])
                if resource["kind"] not in {"reference", "template", "image"}:
                    raise ValueError("Skill 资料类型不受支持")
            except ValueError as error:
                raise WorkflowError("invalid_agent_skill_package", str(error), 422) from None
        if metadata["issues"]:
            raise WorkflowError("agent_skill_incompatible", "；".join(metadata["issues"]), 422)

    def _write_settings(self, owner, key, *, row=None, enabled=None):
        setting = skill_setting(self.session, owner, key, lock=True)
        if setting is None:
            setting = AgentSkillSetting(
                id=next_id(),
                owner_user_id=owner,
                skill_key=key,
                skill_id=row.id if row else None,
                enabled=int(enabled if enabled is not None else bool(row.enabled) if row else True),
                row_version=1,
                updated_at=utcnow(),
            )
            self.session.add(setting)
        else:
            if setting.row_version == UINT64_MAX:
                raise Conflict("Skill 设置版本已达到上限")
            if enabled is not None:
                setting.enabled = int(enabled)
            setting.row_version += 1
            setting.updated_at = utcnow()
        if row is not None and enabled is not None:
            row.enabled = int(enabled)
        return setting
