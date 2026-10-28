from __future__ import annotations

import hashlib
from pathlib import PurePath

from pydantic import ValidationError
from sqlalchemy import and_, func, select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.skill_catalog import BUILTIN_NAMES as BUILTIN_NAMES
from short_drama.domain.assistant.context.context import AgentSkill
from short_drama.domain.identity.accounts.user import User
from short_drama.domain.skills.catalog.skill import AgentSkillSetting, AgentSkillVersion
from short_drama.schemas.assistant.context.context import SkillRead
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.skills.catalog.skill import (
    MAX_SKILL_BYTES,
    SkillResourceRead,
    SkillSummary,
)
from short_drama.service.base import utcnow
from short_drama.service.skills.packages.skill_package import (
    local_reference_issues,
    package_digest,
    platform_tool_issues,
)
from short_drama.utils.snowflake import next_id

"""Account skill directory, immutable text packages and explicit version publication."""


def skill_digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_resource_manifest(item: dict) -> SkillResourceRead:
    try:
        return SkillResourceRead.model_validate(
            {key: item[key] for key in ("path", "kind", "byte_size", "checksum_sha256")}
        )
    except ValidationError as error:
        raise WorkflowError(
            "invalid_agent_skill_resource", "Skill 资料格式已不受支持，请重新导入或发布", 409
        ) from error


def skill_issues(metadata: dict, instructions: str, resources: list[dict]) -> list[str]:
    issues = platform_tool_issues(metadata.get("required_tools", []))
    issues.extend(
        local_reference_issues(
            instructions,
            resources,
        )
    )
    return issues


def builtin_skills(session, *, summary=False):
    version = AgentSkillVersion
    latest = (
        select(version.skill_key, func.max(version.content_version).label("number"))
        .where(version.owner_user_id.is_(None), version.skill_key.in_(BUILTIN_NAMES))
        .group_by(version.skill_key)
        .subquery()
    )
    query = select(
        version.skill_key,
        version.content_version,
        version.package_metadata,
        version.checksum_sha256,
        version.created_at,
        func.json_length(version.resources).label("resource_count"),
    ).join(
        latest,
        and_(version.skill_key == latest.c.skill_key, version.content_version == latest.c.number),
    )
    if not summary:
        query = query.add_columns(version.instructions, version.resources)
    result = {}
    for row in session.execute(query).mappings():
        metadata = dict(row["package_metadata"])
        issues = metadata.pop("issues", [])
        checksum = metadata.pop("instructions_checksum_sha256", None)
        # 旧迁移只回填短提示；目录必须由完整方法包显式发布。
        if not checksum:
            continue
        values = dict(
            **metadata,
            id=row["skill_key"],
            filename=None,
            builtin=True,
            content_version=row["content_version"],
            row_version=None,
            enabled=True,
            checksum_sha256=checksum,
            package_checksum=row["checksum_sha256"],
            compatible=not issues,
            issues=issues,
            resource_count=row["resource_count"],
            updated_at=row["created_at"],
        )
        if summary:
            result[row["skill_key"]] = SkillSummary.model_validate(values)
        else:
            values.update(
                instructions=row["instructions"],
                checksum_sha256=skill_digest(row["instructions"]),
                resources=[read_resource_manifest(item) for item in row["resources"]],
            )
            result[row["skill_key"]] = SkillRead.model_validate(values)
    return [result[key] for key in BUILTIN_NAMES if key in result]


def current_version(session, skill_key: str, version: int):
    return session.scalar(
        select(AgentSkillVersion).where(
            AgentSkillVersion.skill_key == skill_key,
            AgentSkillVersion.content_version == version,
        )
    )


def skill_setting(session, owner: int, key: str, *, lock: bool = False):
    query = select(AgentSkillSetting).where(
        AgentSkillSetting.owner_user_id == owner, AgentSkillSetting.skill_key == key
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.scalar(query)


def lock_skill_owner(session, owner: int) -> None:
    if session.scalar(select(User.id).where(User.id == owner).with_for_update()) is None:
        raise NotFound("Skill 账号不存在")


def publish_version_locked(session, row, metadata: dict, instructions: str, resources: list[dict]):
    version = AgentSkillVersion(
        id=next_id(),
        skill_key=str(row.id),
        skill_id=row.id,
        owner_user_id=row.owner_user_id,
        content_version=row.content_version,
        package_metadata=metadata,
        instructions=instructions,
        resources=resources,
        checksum_sha256=package_digest(metadata, instructions, resources),
        created_at=utcnow(),
    )
    session.add(version)
    return version


def read_skill(row, version=None, setting=None, *, summary=False, resource_count=0):
    if version is None:
        raise WorkflowError("agent_skill_version_missing", "Skill 内容版本不存在，请重新导入", 409)
    metadata = dict(version.package_metadata)
    stored_issues = metadata.pop("issues", [])
    resources = [] if summary else version.resources
    values = dict(
        **metadata,
        id=str(row.id),
        filename=row.filename,
        builtin=False,
        content_version=row.content_version,
        row_version=row.row_version,
        enabled=bool(setting.enabled if setting else row.enabled),
        settings_version=setting.row_version if setting else 0,
        checksum_sha256=row.checksum_sha256,
        package_checksum=version.checksum_sha256,
        resource_count=resource_count if summary else len(resources),
        updated_at=row.updated_at,
    )
    values["name"] = row.name
    if summary:
        values.update(issues=stored_issues, compatible=not stored_issues)
        return SkillSummary.model_validate(values)
    values.update(
        instructions=version.instructions,
        resources=[read_resource_manifest(item) for item in resources],
    )
    values["issues"] = skill_issues(metadata, values["instructions"], resources)
    values["compatible"] = not values["issues"]
    return SkillRead.model_validate(values)


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
    lock_skill_owner(session, owner_user_id)
    builtins = {item.id: item for item in builtin_skills(session)}
    frozen = []
    for selection in selections:
        if selection.id in builtins:
            skill = builtins[selection.id]
            setting = skill_setting(session, owner_user_id, selection.id, lock=True)
            if setting:
                skill = skill.model_copy(
                    update={
                        "enabled": bool(setting.enabled),
                        "settings_version": setting.row_version,
                    }
                )
            version = current_version(session, selection.id, skill.content_version)
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
            version = current_version(session, str(row.id), row.content_version)
            skill = read_skill(
                row, version, skill_setting(session, owner_user_id, str(row.id), lock=True)
            )
        if not skill.enabled:
            raise WorkflowError("agent_skill_disabled", "所选技能已停用，请重新选择", 422)
        if not skill.compatible:
            raise WorkflowError(
                "agent_skill_incompatible", "所选技能依赖平台未提供的能力，请先适配", 422
            )
        if skill.content_version != selection.content_version:
            raise Conflict("技能内容已更新，请核对新版本后重新加载")
        item = skill.model_dump(mode="json")
        if version:
            item["version_id"] = str(version.id)
        frozen.append(item)
    if sum(len(item["instructions"].encode("utf-8")) for item in frozen) > 131072:
        raise WorkflowError("agent_skills_too_large", "已加载技能正文合计不能超过 128 KiB", 422)
    return frozen
