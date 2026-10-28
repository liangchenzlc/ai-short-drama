from __future__ import annotations

import json

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import defer

from short_drama.core.exceptions import WorkflowError
from short_drama.core.skill_catalog import BUILTIN_NAMES as BUILTIN_NAMES
from short_drama.domain.assistant.context.context import AgentSkill
from short_drama.domain.skills.catalog.skill import AgentSkillSetting, AgentSkillVersion
from short_drama.schemas.skills.catalog.skill import (
    SkillSummary,
)
from short_drama.service.base import Page
from short_drama.service.skills.catalog.base import SkillServiceBase
from short_drama.service.skills.catalog.versions import (
    builtin_skills,
    read_skill,
)


class SkillListing(SkillServiceBase):
    def list(self, offset=0, limit=50, **filters):
        owner = self._actor().user_id
        with self._transaction(read_only=True):
            return self._list_locked(owner, offset, limit, **filters)

    def _list_locked(
        self,
        owner,
        offset=0,
        limit=50,
        *,
        query=None,
        source=None,
        enabled=None,
        mode=None,
        compatible_only=False,
        exclude_keys=(),
    ):
        self.dao.validate_pagination(offset, limit)
        if source not in {None, "builtin", "personal"}:
            raise WorkflowError("invalid_agent_skill_filter", "Skill 筛选条件不正确", 422)
        if (
            mode not in {None, "standard", "infinite_canvas"}
            or query is not None
            and len(query) > 120
        ):
            raise WorkflowError("invalid_agent_skill_filter", "Skill 搜索或适用模式不正确", 422)
        settings = {
            row.skill_key: row
            for row in self.session.scalars(
                select(AgentSkillSetting).where(
                    AgentSkillSetting.owner_user_id == owner,
                    AgentSkillSetting.skill_key.in_(BUILTIN_NAMES),
                )
            )
        }
        builtin = []
        for item in builtin_skills(self.session, summary=True) if source != "personal" else []:
            if item.id in exclude_keys:
                continue
            setting = settings.get(item.id)
            if setting:
                item = item.model_copy(
                    update={
                        "enabled": bool(setting.enabled),
                        "settings_version": setting.row_version,
                    }
                )
            if enabled is not None and item.enabled != enabled:
                continue
            if mode is not None and mode not in item.modes:
                continue
            if compatible_only and not item.compatible:
                continue
            if query and query.casefold() not in " ".join([item.name, item.description]).casefold():
                continue
            builtin.append(
                SkillSummary.model_validate(item.model_dump(exclude={"instructions", "resources"}))
            )
        version, setting = AgentSkillVersion, AgentSkillSetting
        joined = (
            select(
                AgentSkill,
                version,
                setting,
                func.coalesce(func.json_length(version.resources), 0),
            )
            .outerjoin(
                version,
                and_(
                    version.skill_id == AgentSkill.id,
                    version.content_version == AgentSkill.content_version,
                ),
            )
            .outerjoin(
                setting, and_(setting.skill_id == AgentSkill.id, setting.owner_user_id == owner)
            )
        )
        conditions = [AgentSkill.owner_user_id == owner, AgentSkill.deleted_at.is_(None)]
        excluded_ids = [int(key) for key in exclude_keys if key.isdecimal()]
        if excluded_ids:
            conditions.append(AgentSkill.id.not_in(excluded_ids))
        if compatible_only:
            conditions.append(
                or_(version.id.is_(None), func.json_length(version.package_metadata["issues"]) == 0)
            )
        if source == "builtin":
            conditions.append(AgentSkill.id.is_(None))
        if enabled is not None:
            conditions.append(func.coalesce(setting.enabled, AgentSkill.enabled) == int(enabled))
        if mode is not None:
            conditions.append(
                or_(
                    version.id.is_(None),
                    func.json_contains(version.package_metadata["modes"], json.dumps(mode)) == 1,
                )
            )
        if query:
            conditions.append(
                or_(
                    AgentSkill.name.contains(query, autoescape=True),
                    func.json_unquote(version.package_metadata["description"]).contains(
                        query, autoescape=True
                    ),
                )
            )
        joined = joined.where(*conditions)
        total = self.session.scalar(
            select(func.count()).select_from(joined.with_only_columns(AgentSkill.id).subquery())
        )
        items = builtin[offset : offset + limit]
        remaining = limit - len(items)
        if remaining:
            rows = self.session.execute(
                joined.options(
                    defer(AgentSkill.instructions),
                    defer(version.instructions),
                    defer(version.resources),
                )
                .order_by(AgentSkill.updated_at.desc(), AgentSkill.id.desc())
                .offset(max(0, offset - len(builtin)))
                .limit(remaining)
            ).all()
            items += [
                read_skill(row, revision, preference, summary=True, resource_count=count)
                for row, revision, preference, count in rows
            ]
        return Page(items=items, total=total + len(builtin), offset=offset, limit=limit)

    def detail(self, identifier):
        owner = self._actor().user_id
        with self._transaction(read_only=True):
            if str(identifier) in BUILTIN_NAMES:
                return self._builtin(str(identifier), owner)
            return self._detail(self._skill(identifier))
