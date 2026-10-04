"""Owner-only Markdown skills; accepted runs retain immutable instruction snapshots."""

import hashlib
from pathlib import PurePath

from sqlalchemy import func, select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.domain.agent_context import AgentSkill
from short_drama.schemas.agent_context import SkillDelete, SkillPatch, SkillRead
from short_drama.schemas.base import parse_identifier
from short_drama.service.base import BaseService, Page, utcnow

MAX_SKILL_BYTES = 65536
BUILTIN_NAMES = {
    "novel.v1": "小说创作",
    "script.v1": "剧本创作",
    "extract.v1": "素材提取",
    "storyboard.v1": "分镜制作",
    "asset_patch.v1": "素材修改",
    "shot_patch.v1": "镜头修改",
    "image.v1": "图片创作",
    "video.v1": "视频创作",
}


def skill_digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def builtin_skills():
    from short_drama.agent.tools import SKILLS

    return [
        SkillRead(
            id=name,
            name=BUILTIN_NAMES[name],
            filename=None,
            builtin=True,
            content_version=1,
            row_version=None,
            enabled=True,
            instructions=instructions,
            checksum_sha256=skill_digest(instructions),
        )
        for name, instructions in SKILLS.items()
    ]


def read_skill(row):
    return SkillRead(
        id=str(row.id),
        name=row.name,
        filename=row.filename,
        builtin=False,
        content_version=row.content_version,
        row_version=row.row_version,
        enabled=bool(row.enabled),
        instructions=row.instructions,
        checksum_sha256=row.checksum_sha256,
    )


def markdown_content(data, filename):
    name = PurePath(filename.replace("\\", "/")).name
    if not name.lower().endswith(".md") or not 0 < len(data) <= MAX_SKILL_BYTES:
        raise WorkflowError("invalid_agent_skill", "技能须为不超过 64 KiB 的 Markdown 文件", 422)
    try:
        content = data.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        raise WorkflowError("invalid_agent_skill", "技能文件须使用 UTF-8 编码", 422) from None
    if not content or "\x00" in content:
        raise WorkflowError("invalid_agent_skill", "技能文件须包含有效 Markdown 正文", 422)
    return name[:255], content


def freeze_skills(session, owner_user_id, selections):
    """Called inside the caller's transaction; selected versions cannot drift."""
    builtins = {item.id: item for item in builtin_skills()}
    frozen = []
    for selection in selections:
        if selection.id in builtins:
            skill = builtins[selection.id]
        else:
            try:
                identifier = parse_identifier(selection.id)
            except ValueError:
                raise NotFound("Skill does not exist") from None
            row = session.scalar(
                select(AgentSkill)
                .where(
                    AgentSkill.id == identifier,
                    AgentSkill.owner_user_id == owner_user_id,
                    AgentSkill.deleted_at.is_(None),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise NotFound("Skill does not exist")
            skill = read_skill(row)
        if not skill.enabled:
            raise WorkflowError("agent_skill_disabled", "所选技能已停用，请重新选择", 422)
        if skill.content_version != selection.content_version:
            raise Conflict("技能内容已更新，请核对新版本后重新加载")
        frozen.append(skill.model_dump(mode="json"))
    if sum(len(item["instructions"].encode("utf-8")) for item in frozen) > 131072:
        raise WorkflowError("agent_skills_too_large", "已加载技能正文合计不能超过 128 KiB", 422)
    return frozen


class AgentSkillService(BaseService):
    model = AgentSkill

    def __init__(self, session, settings):
        super().__init__(session)
        self.settings = settings

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

    def list(self, offset=0, limit=50):
        owner = self._actor().user_id
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            builtin = builtin_skills()
            conditions = (AgentSkill.owner_user_id == owner, AgentSkill.deleted_at.is_(None))
            total = self.session.scalar(select(func.count(AgentSkill.id)).where(*conditions))
            items = builtin[offset : offset + limit]
            remaining = limit - len(items)
            if remaining:
                rows = self.session.scalars(
                    select(AgentSkill)
                    .where(*conditions)
                    .order_by(AgentSkill.updated_at.desc(), AgentSkill.id.desc())
                    .offset(max(0, offset - len(builtin)))
                    .limit(remaining)
                ).all()
                items += [read_skill(row) for row in rows]
            return Page(items=items, total=total + len(builtin), offset=offset, limit=limit)

    def upload(self, stream, filename):
        actor = self._actor()
        name, instructions = markdown_content(stream.read(MAX_SKILL_BYTES + 1), filename)
        with self._transaction():
            now = utcnow()
            row = self.dao.create(
                {
                    "owner_user_id": actor.user_id,
                    "name": name.removesuffix(".md")[:120],
                    "filename": name,
                    "instructions": instructions,
                    "content_version": 1,
                    "row_version": 1,
                    "checksum_sha256": skill_digest(instructions),
                    "enabled": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            return read_skill(row)

    def detail(self, identifier):
        if str(identifier) in BUILTIN_NAMES:
            self._actor()
            return next(item for item in builtin_skills() if item.id == str(identifier))
        with self._transaction(read_only=True):
            return read_skill(self._skill(identifier))

    def patch(self, identifier, payload):
        values = self._payload(SkillPatch, payload)
        with self._transaction():
            row = self._skill(identifier, lock=True)
            if row.row_version != values.pop("row_version"):
                raise Conflict("技能已被修改，请先核对最新内容")
            if "instructions" in values and values["instructions"] != row.instructions:
                row.content_version += 1
                row.checksum_sha256 = skill_digest(values["instructions"])
            for name, value in values.items():
                setattr(row, name, int(value) if name == "enabled" else value)
            row.row_version += 1
            row.updated_at = utcnow()
            return read_skill(row)

    def delete(self, identifier, payload):
        expected = self._payload(SkillDelete, payload)["row_version"]
        with self._transaction():
            row = self._skill(identifier, lock=True)
            if row.row_version != expected:
                raise Conflict("技能已被修改，请先核对最新内容")
            row.deleted_at = row.updated_at = utcnow()
            row.enabled = 0
            row.row_version += 1
