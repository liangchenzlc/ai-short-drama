from __future__ import annotations

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.core.skill_catalog import BUILTIN_NAMES as BUILTIN_NAMES
from short_drama.db.access.identity import scoped_key
from short_drama.domain.assistant.context.context import AgentSkill
from short_drama.schemas.assistant.context.context import SkillRead
from short_drama.schemas.skills.catalog.skill import (
    MAX_SKILL_BYTES,
)
from short_drama.service.skills.catalog.base import SkillServiceBase
from short_drama.service.skills.catalog.versions import (
    markdown_content,
)
from short_drama.service.skills.packages.skill_package import (
    MAX_IMPORT_BYTES,
    parse_skill_import,
)


class SkillImports(SkillServiceBase):
    def upload(self, stream, filename):
        self._actor()
        data = stream.read(MAX_SKILL_BYTES + 1)
        name, _instructions = markdown_content(data, filename)
        draft = parse_skill_import(data, name).draft.model_dump()
        draft["modes"] = ["standard", "infinite_canvas"]
        return self.create(draft, filename=name)

    def preview_import(self, stream, filename):
        self._actor()
        return parse_skill_import(stream.read(MAX_IMPORT_BYTES + 1), filename)

    def request_receipt(self, key: str) -> SkillRead:
        """Read an account-scoped create receipt without replaying a retired payload."""
        owner = self._actor().user_id
        key = scoped_key(self.session, key)
        with self._transaction(read_only=True):
            row = self.session.scalar(
                select(AgentSkill).where(
                    AgentSkill.owner_user_id == owner, AgentSkill.create_key == key
                )
            )
            if row is None:
                raise NotFound("尚未找到原请求的保存结果")
            if row.deleted_at is not None:
                raise Conflict("原请求已保存，但此 Skill 后来已被删除")
            return self._detail(row)

    def _replay(self, owner, key, request_hash):
        if key is None:
            return None
        row = self.session.scalar(
            select(AgentSkill)
            .where(
                AgentSkill.owner_user_id == owner,
                AgentSkill.create_key == key,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        if row.create_hash != request_hash:
            raise Conflict("该 Skill 创建身份已用于不同内容，请保留原请求核对")
        if row.deleted_at is not None:
            raise Conflict("原创建的 Skill 已删除，不能以旧创建身份恢复")
        return self._detail(row)
