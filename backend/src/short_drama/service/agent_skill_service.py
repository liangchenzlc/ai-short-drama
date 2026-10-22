"""Account skill directory, immutable text packages and explicit version publication."""

import hashlib
import io
import json
from pathlib import PurePath
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import defer

from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.db.access import scoped_key
from short_drama.domain.agent_context import AgentSkill
from short_drama.domain.agent_skill import AgentSkillSetting, AgentSkillVersion
from short_drama.domain.collaboration import User
from short_drama.schemas.agent_context import SkillDelete, SkillPatch, SkillRead
from short_drama.schemas.agent_skill import (
    MAX_PACKAGE_BYTES,
    MAX_RESOURCE_BYTES,
    MAX_SKILL_BYTES,
    SkillCopy,
    SkillCreate,
    SkillMetadata,
    SkillResourceContent,
    SkillResourceInput,
    SkillSettingsPatch,
    SkillSummary,
    resource_path,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.service.agent_skill_package import (
    MAX_IMPORT_BYTES,
    local_reference_issues,
    package_digest,
    package_markdown,
    parse_skill_import,
    platform_tool_issues,
    validate_package_size,
)
from short_drama.service.agent_skill_resource_service import (
    stage_object_key,
    validate_staged_resources_locked,
)
from short_drama.service.base import BaseService, Page, utcnow
from short_drama.utils.snowflake import next_id

BUILTIN_NAMES = {
    "novel.v1": "小说创作",
    "script.v1": "剧本创作",
    "extract.v1": "素材提取",
    "storyboard.v1": "分镜制作",
    "asset_patch.v1": "素材修改",
    "shot_patch.v1": "镜头修改",
    "image.v1": "图片创作",
    "video.v1": "视频创作",
    "planning.v1": "创作任务规划",
    "voice.v1": "配音与声音规划",
    "canvas.v1": "画布流程搭建",
    "continuity.v1": "连续性检查",
}


def skill_digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def legacy_metadata(row) -> dict:
    metadata = SkillMetadata(
        name=row.name,
        slug=f"personal-{row.id}",
        description=f"个人创作方法：{row.name}",
    ).model_dump(mode="json")
    metadata["issues"] = []
    return metadata


def skill_issues(metadata: dict, instructions: str, resources: list[dict]) -> list[str]:
    issues = platform_tool_issues(metadata.get("required_tools", []))
    issues.extend(
        local_reference_issues(
            instructions,
            [
                SkillResourceInput(path=item["path"], kind=item["kind"], content="")
                for item in resources
            ],
        )
    )
    return issues


def builtin_skills(session=None, *, summary=False):
    from short_drama.agent.tools import SKILLS

    items = [
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
            slug=name.replace(".", "-"),
            description=instructions,
            source="平台内置方法；保留历史兼容身份",
        )
        for name, instructions in SKILLS.items()
    ]
    for item in items:
        metadata = {
            key: value
            for key, value in item.model_dump().items()
            if key in SkillMetadata.model_fields
        }
        metadata["issues"] = []
        item.package_checksum = package_digest(metadata, item.instructions, [])
    result = {item.id: item for item in items}
    if session is not None:
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
            and_(
                version.skill_key == latest.c.skill_key, version.content_version == latest.c.number
            ),
        )
        if not summary:
            query = query.add_columns(version.instructions, version.resources)
        for row in session.execute(query).mappings():
            metadata = dict(row["package_metadata"])
            issues = metadata.pop("issues", [])
            checksum = metadata.pop("instructions_checksum_sha256", None)
            legacy = result.get(row["skill_key"])
            values = dict(
                **metadata,
                id=row["skill_key"],
                filename=None,
                builtin=True,
                content_version=row["content_version"],
                row_version=None,
                enabled=True,
                checksum_sha256=checksum or (legacy.checksum_sha256 if legacy else ""),
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
                    resources=[
                        {key: item[key] for key in ("path", "kind", "byte_size", "checksum_sha256")}
                        for item in row["resources"]
                    ],
                )
                result[row["skill_key"]] = SkillRead.model_validate(values)
    return [
        SkillSummary.model_validate(item.model_dump(exclude={"instructions", "resources"}))
        if summary
        else item
        for item in result.values()
    ]


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
    metadata = dict(version.package_metadata if version else legacy_metadata(row))
    stored_issues = metadata.pop("issues", [])
    resources = [] if summary or version is None else version.resources
    values = dict(
        **metadata,
        id=str(row.id),
        filename=row.filename,
        builtin=False,
        content_version=row.content_version,
        row_version=row.row_version,
        enabled=bool(setting.enabled if setting else row.enabled),
        invocation_mode=setting.invocation_mode if setting else "auto",
        settings_version=setting.row_version if setting else 0,
        checksum_sha256=row.checksum_sha256,
        package_checksum=version.checksum_sha256 if version else "",
        resource_count=resource_count if summary else len(resources),
        updated_at=row.updated_at,
    )
    values["name"] = row.name
    if summary:
        values.update(issues=stored_issues, compatible=not stored_issues)
        return SkillSummary.model_validate(values)
    values.update(
        instructions=version.instructions if version else row.instructions,
        resources=[
            {key: item[key] for key in ("path", "kind", "byte_size", "checksum_sha256")}
            for item in resources
        ],
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
                        "invocation_mode": setting.invocation_mode,
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
            if version is None:
                version = publish_version_locked(
                    session, row, legacy_metadata(row), row.instructions, []
                )
                session.flush()
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


class AgentSkillService(BaseService):
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
                    "invocation_mode": setting.invocation_mode,
                    "settings_version": setting.row_version,
                }
            )
        return skill

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
        invocation_mode=None,
        mode=None,
        compatible_only=False,
        exclude_keys=(),
    ):
        self.dao.validate_pagination(offset, limit)
        if source not in {None, "builtin", "personal"} or invocation_mode not in {
            None,
            "auto",
            "manual",
        }:
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
                        "invocation_mode": setting.invocation_mode,
                        "settings_version": setting.row_version,
                    }
                )
            if enabled is not None and item.enabled != enabled:
                continue
            if invocation_mode is not None and item.invocation_mode != invocation_mode:
                continue
            if mode is not None and mode not in item.modes:
                continue
            if compatible_only and not item.compatible:
                continue
            if (
                query
                and query.casefold()
                not in " ".join([item.name, item.slug, item.description, *item.tags]).casefold()
            ):
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
        if invocation_mode is not None:
            conditions.append(func.coalesce(setting.invocation_mode, "auto") == invocation_mode)
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
                    func.json_unquote(version.package_metadata["slug"]).contains(
                        query, autoescape=True
                    ),
                    func.json_unquote(version.package_metadata["tags"]).contains(
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

    def upload(self, stream, filename):
        self._actor()
        data = stream.read(MAX_SKILL_BYTES + 1)
        name, _instructions = markdown_content(data, filename)
        draft = parse_skill_import(data, name).draft.model_dump()
        if not draft["description"]:
            draft["description"] = f"个人创作方法：{draft['name']}"
        return self.create(draft, filename=name)

    def preview_import(self, stream, filename):
        self._actor()
        return parse_skill_import(stream.read(MAX_IMPORT_BYTES + 1), filename)

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

    def _resource_bytes(self, item):
        if self.storage is None:
            raise WorkflowError("agent_skill_storage_unavailable", "Skill 资料存储未配置", 503)
        try:
            with self.storage.open(item["bucket"], item["object_name"]) as response:
                data = response.read(MAX_RESOURCE_BYTES + 1)
        except (BusinessError, OSError) as error:
            raise WorkflowError(
                "agent_skill_resource_unavailable",
                f"读取 Skill 资料“{item['path']}”失败，请核对存储状态",
                503,
            ) from error
        if (
            len(data) != item["byte_size"]
            or hashlib.sha256(data).hexdigest() != item["checksum_sha256"]
        ):
            raise WorkflowError(
                "agent_skill_resource_corrupt", f"Skill 资料“{item['path']}”体积或校验值不一致", 409
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise WorkflowError(
                "agent_skill_resource_corrupt", f"Skill 资料“{item['path']}”不是 UTF-8 文本", 409
            ) from None

    def _store_resources(self, owner, resources, *, staged=None):
        if resources and self.storage is None:
            raise WorkflowError(
                "agent_skill_storage_unavailable", "发布 Skill 资料需要配置对象存储", 503
            )
        manifest = []
        staged = {} if staged is None else staged
        for resource in resources:
            data = resource.content.encode("utf-8")
            digest = hashlib.sha256(data).hexdigest()
            try:
                stored = staged.get(digest)
                if stored is None:
                    stored = self.storage.put(
                        self.settings.minio_image_bucket,
                        stage_object_key(owner, digest),
                        io.BytesIO(data),
                        len(data),
                        "text/plain; charset=utf-8",
                    )
            except (BusinessError, OSError) as error:
                raise WorkflowError(
                    "agent_skill_storage_unavailable",
                    f"发布 Skill 资料“{resource.path}”失败，请核对存储状态",
                    503,
                ) from error
            item = dict(
                path=resource.path,
                kind=resource.kind,
                byte_size=len(data),
                checksum_sha256=digest,
                bucket=stored.bucket,
                object_name=stored.object_name,
            )
            self._resource_bytes(item)
            staged[digest] = stored
            manifest.append(item)
        return manifest

    def _metadata(self, content):
        metadata = content.model_dump(
            mode="json", exclude={"instructions", "resources", "enabled", "invocation_mode"}
        )
        manifest = [{"path": item.path, "kind": item.kind} for item in content.resources]
        metadata["issues"] = list(
            dict.fromkeys(
                skill_issues(metadata, content.instructions, manifest)
                + local_reference_issues(content.instructions, content.resources)
            )
        )
        return metadata

    def _write_settings(self, owner, key, *, row=None, enabled=None, invocation_mode=None):
        setting = skill_setting(self.session, owner, key, lock=True)
        if setting is None:
            setting = AgentSkillSetting(
                id=next_id(),
                owner_user_id=owner,
                skill_key=key,
                skill_id=row.id if row else None,
                enabled=int(enabled if enabled is not None else bool(row.enabled) if row else True),
                invocation_mode=invocation_mode or "auto",
                row_version=1,
                updated_at=utcnow(),
            )
            self.session.add(setting)
        else:
            if setting.row_version == UINT64_MAX:
                raise Conflict("Skill 设置版本已达到上限")
            if enabled is not None:
                setting.enabled = int(enabled)
            if invocation_mode is not None:
                setting.invocation_mode = invocation_mode
            setting.row_version += 1
            setting.updated_at = utcnow()
        if row is not None and enabled is not None:
            row.enabled = int(enabled)
        return setting

    def create(self, payload, idempotency_key=None, *, filename=None, _source=None, _identity=None):
        owner = self._actor().user_id
        content = SkillCreate.model_validate(self._payload(SkillCreate, payload))
        request_hash = skill_digest(
            json.dumps(
                _identity or {"content": content.model_dump(mode="json"), "filename": filename},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        key = scoped_key(self.session, idempotency_key) if idempotency_key is not None else None
        with self._transaction(read_only=True):
            replay = self._replay(owner, key, request_hash)
            if replay is not None:
                return replay
        metadata = self._metadata(content)
        validate_package_size(
            metadata,
            content.instructions,
            [
                {"path": item.path, "kind": item.kind, "byte_size": len(item.content.encode())}
                for item in content.resources
            ],
        )
        manifest = self._store_resources(owner, content.resources)
        with self._transaction():
            lock_skill_owner(self.session, owner)
            replay = self._replay(owner, key, request_hash)
            if replay is not None:
                return replay
            if _source is not None:
                source = (
                    self._builtin(_source[0], owner)
                    if _source[0] in BUILTIN_NAMES
                    else self._detail(self._skill(_source[0], lock=True))
                )
                if source.content_version != _source[1]:
                    raise Conflict("复制来源已更新，请核对新版本后重试")
            validate_staged_resources_locked(self.session, manifest)
            now = utcnow()
            row = self.dao.create(
                {
                    "owner_user_id": owner,
                    "name": content.name,
                    "filename": filename or f"{content.slug}.md",
                    "instructions": content.instructions,
                    "content_version": 1,
                    "row_version": 1,
                    "checksum_sha256": skill_digest(content.instructions),
                    "enabled": int(content.enabled),
                    "create_key": key,
                    "create_hash": request_hash if key is not None else None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            version = publish_version_locked(
                self.session, row, metadata, content.instructions, manifest
            )
            setting = self._write_settings(
                owner,
                str(row.id),
                row=row,
                enabled=content.enabled,
                invocation_mode=content.invocation_mode,
            )
            self.session.flush()
            return read_skill(row, version, setting)

    def detail(self, identifier):
        owner = self._actor().user_id
        with self._transaction(read_only=True):
            if str(identifier) in BUILTIN_NAMES:
                return self._builtin(str(identifier), owner)
            return self._detail(self._skill(identifier))

    def patch(self, identifier, payload):
        if str(identifier) in BUILTIN_NAMES:
            raise WorkflowError(
                "agent_skill_readonly", "内置 Skill 原件只读，请先复制为个人 Skill", 403
            )
        values = self._payload(SkillPatch, payload)
        owner = self._actor().user_id
        with self._transaction(read_only=True):
            row = self._skill(identifier)
            if row.row_version != values["row_version"]:
                raise Conflict("技能已被修改，请先核对最新内容")
            original = self._detail(row)
            version = current_version(self.session, str(row.id), row.content_version)
            old_manifest = list(version.resources) if version else []
            original_checksum = (
                version.checksum_sha256
                if version
                else package_digest(legacy_metadata(row), row.instructions, [])
            )
        fields = set(SkillMetadata.model_fields) | {
            "instructions",
            "resources",
            "enabled",
            "invocation_mode",
        }
        basis = {key: value for key, value in original.model_dump().items() if key in fields}
        basis["resources"] = [
            dict(path=item["path"], kind=item["kind"], content="") for item in old_manifest
        ]
        basis.update({key: value for key, value in values.items() if key != "row_version"})
        content = SkillCreate.model_validate(basis)
        manifest = (
            self._store_resources(owner, content.resources)
            if "resources" in values
            else old_manifest
        )
        if (
            len(content.instructions.encode()) + sum(item["byte_size"] for item in manifest)
            > MAX_PACKAGE_BYTES
        ):
            raise WorkflowError(
                "agent_skill_package_too_large", "Skill 文本与资料合计不能超过 1 MiB", 422
            )
        metadata = self._metadata(content)
        validate_package_size(metadata, content.instructions, manifest)
        with self._transaction():
            lock_skill_owner(self.session, owner)
            row = self._skill(identifier, lock=True)
            if row.row_version != values["row_version"]:
                raise Conflict("技能已被修改，请先核对最新内容")
            if row.row_version == UINT64_MAX or row.content_version == UINT64_MAX:
                raise Conflict("Skill 保存版本已达到上限")
            version = current_version(self.session, str(row.id), row.content_version)
            if version is None:
                version = publish_version_locked(
                    self.session, row, legacy_metadata(row), row.instructions, []
                )
            if package_digest(metadata, content.instructions, manifest) != original_checksum:
                if "resources" in values:
                    validate_staged_resources_locked(self.session, manifest)
                row.content_version += 1
                row.instructions = content.instructions
                row.checksum_sha256 = skill_digest(content.instructions)
                version = publish_version_locked(
                    self.session, row, metadata, content.instructions, manifest
                )
            row.name = content.name
            setting = skill_setting(self.session, owner, str(row.id))
            if {"enabled", "invocation_mode"}.intersection(values):
                setting = self._write_settings(
                    owner,
                    str(row.id),
                    row=row,
                    enabled=values.get("enabled"),
                    invocation_mode=values.get("invocation_mode"),
                )
            row.row_version += 1
            row.updated_at = utcnow()
            self.session.flush()
            return read_skill(row, version, setting)

    def patch_settings(self, identifier, payload):
        values = SkillSettingsPatch.model_validate(self._payload(SkillSettingsPatch, payload))
        owner = self._actor().user_id
        key = str(identifier)
        with self._transaction():
            lock_skill_owner(self.session, owner)
            row = None if key in BUILTIN_NAMES else self._skill(key, lock=True)
            setting = skill_setting(self.session, owner, key, lock=True)
            if (setting.row_version if setting else 0) != values.row_version:
                raise Conflict("Skill 调用设置已更新，请核对最新设置")
            self._write_settings(
                owner, key, row=row, enabled=values.enabled, invocation_mode=values.invocation_mode
            )
            self.session.flush()
            return self._builtin(key, owner) if row is None else self._detail(row)

    def resource_content(self, identifier, content_version, path, *, offset=0, limit=6000):
        owner = self._actor().user_id
        try:
            path = resource_path(path)
        except ValueError:
            raise NotFound("Skill 资料不存在") from None
        if not 0 <= offset <= MAX_RESOURCE_BYTES or not 1 <= limit <= 6000:
            raise WorkflowError("invalid_agent_skill_range", "资料读取范围不正确", 422)
        number = parse_identifier(content_version)
        with self._transaction(read_only=True):
            if str(identifier) not in BUILTIN_NAMES:
                self._skill(identifier)
            version = current_version(self.session, str(identifier), number)
            if version is None or version.owner_user_id not in {None, owner}:
                raise NotFound("Skill 内容版本不存在")
            item = next((dict(item) for item in version.resources if item["path"] == path), None)
            if item is None:
                raise NotFound("Skill 资料不存在")
        text = self._resource_bytes(item)
        end = min(len(text), offset + limit)
        return SkillResourceContent(
            skill_id=str(identifier),
            content_version=number,
            **{key: item[key] for key in ("path", "kind", "byte_size", "checksum_sha256")},
            offset=offset,
            content=text[offset:end],
            next_offset=end if end < len(text) else None,
        )

    def copy(self, identifier, payload, idempotency_key):
        values = SkillCopy.model_validate(self._payload(SkillCopy, payload))
        owner = self._actor().user_id
        identity = {"copy_from": str(identifier), **values.model_dump(mode="json")}
        request_hash = skill_digest(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        key = scoped_key(self.session, idempotency_key)
        with self._transaction(read_only=True):
            replay = self._replay(owner, key, request_hash)
            if replay is not None:
                return replay
        original = self.detail(identifier)
        if original.content_version != values.content_version:
            raise Conflict("复制来源已更新，请核对新版本后重试")
        resources = []
        for item in original.resources:
            chunks, offset = [], 0
            while True:
                part = self.resource_content(
                    identifier, values.content_version, item.path, offset=offset
                )
                chunks.append(part.content)
                if part.next_offset is None:
                    break
                offset = part.next_offset
            resources.append(dict(path=item.path, kind=item.kind, content="".join(chunks)))
        content = {
            key: value
            for key, value in original.model_dump().items()
            if key in SkillMetadata.model_fields
        }
        content.update(
            name=values.name or f"{original.name}（副本）"[:120],
            instructions=original.instructions,
            resources=resources,
            source=f"复制自 {original.id} 版本 {original.content_version}。{original.source}"[
                :4000
            ],
        )
        return self.create(
            content,
            idempotency_key,
            _source=(str(identifier), values.content_version),
            _identity=identity,
        )

    def export(self, identifier, content_version):
        original = self.detail(identifier)
        if original.content_version != parse_identifier(content_version):
            raise Conflict("Skill 已更新，请核对当前版本后导出")
        with self._transaction(read_only=True):
            if not original.builtin:
                self._skill(identifier)
            version = current_version(self.session, str(identifier), original.content_version)
            manifest = [dict(item) for item in version.resources] if version else []
        resources = [
            dict(path=item["path"], content=self._resource_bytes(item)) for item in manifest
        ]
        metadata = {
            key: value
            for key, value in original.model_dump().items()
            if key in SkillMetadata.model_fields
        }
        text = package_markdown(metadata, original.instructions, manifest)
        data = io.BytesIO()
        with ZipFile(data, "w", ZIP_DEFLATED) as archive:
            archive.writestr("SKILL.md", text)
            for item in resources:
                archive.writestr(item["path"], item["content"])
        return data.getvalue(), f"{original.slug}.zip"

    def delete(self, identifier, payload):
        if str(identifier) in BUILTIN_NAMES:
            raise WorkflowError(
                "agent_skill_readonly", "内置 Skill 原件不能删除，可修改本人调用设置", 403
            )
        expected = self._payload(SkillDelete, payload)["row_version"]
        owner = self._actor().user_id
        with self._transaction():
            lock_skill_owner(self.session, owner)
            row = self._skill(identifier, lock=True)
            if row.row_version != expected:
                raise Conflict("技能已被修改，请先核对最新内容")
            if row.row_version == UINT64_MAX:
                raise Conflict("Skill 保存版本已达到上限")
            row.deleted_at = row.updated_at = utcnow()
            row.enabled = 0
            row.row_version += 1
            self._write_settings(owner, str(row.id), row=row, enabled=False)
