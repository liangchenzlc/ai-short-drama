from __future__ import annotations

import base64
import json

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.core.skill_catalog import BUILTIN_NAMES as BUILTIN_NAMES
from short_drama.db.access.identity import scoped_key
from short_drama.schemas.assistant.context.context import SkillDelete, SkillPatch
from short_drama.schemas.base import UINT64_MAX
from short_drama.schemas.skills.catalog.skill import (
    MAX_PACKAGE_BYTES,
    SkillCopy,
    SkillCreate,
    SkillMetadata,
    SkillSettingsPatch,
)
from short_drama.service.base import utcnow
from short_drama.service.skills.catalog.base import SkillServiceBase
from short_drama.service.skills.catalog.versions import (
    current_version,
    lock_skill_owner,
    publish_version_locked,
    read_skill,
    skill_digest,
    skill_issues,
    skill_setting,
)
from short_drama.service.skills.packages.skill_package import (
    mode_tool_issues,
    package_digest,
    validate_package_size,
)
from short_drama.service.skills.resources.skill_resource import (
    validate_staged_resources_locked,
)


class SkillEditing(SkillServiceBase):
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
        self._validate_write(metadata, [])
        validate_package_size(
            metadata,
            content.instructions,
            [
                {"path": item.path, "kind": item.kind, "byte_size": len(item.resource_bytes())}
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
            )
            self.session.flush()
            return read_skill(row, version, setting)

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
            old_manifest = list(version.resources)
            original_checksum = version.checksum_sha256
        fields = set(SkillMetadata.model_fields) | {
            "instructions",
            "resources",
            "enabled",
        }
        basis = {key: value for key, value in original.model_dump().items() if key in fields}
        basis["resources"] = []
        basis.update({key: value for key, value in values.items() if key != "row_version"})
        content = SkillCreate.model_validate(basis)
        manifest = (
            [
                {"path": item.path, "kind": item.kind, "byte_size": len(item.resource_bytes())}
                for item in content.resources
            ]
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
        if "resources" not in values:
            metadata["issues"] = list(
                dict.fromkeys(
                    skill_issues(metadata, content.instructions, manifest)
                    + mode_tool_issues(content.required_tools, content.modes)
                )
            )
        self._validate_write(metadata, manifest)
        validate_package_size(metadata, content.instructions, manifest)
        if "resources" in values:
            manifest = self._store_resources(owner, content.resources)
        with self._transaction():
            lock_skill_owner(self.session, owner)
            row = self._skill(identifier, lock=True)
            if row.row_version != values["row_version"]:
                raise Conflict("技能已被修改，请先核对最新内容")
            if row.row_version == UINT64_MAX or row.content_version == UINT64_MAX:
                raise Conflict("Skill 保存版本已达到上限")
            version = current_version(self.session, str(row.id), row.content_version)
            if version is None:
                raise WorkflowError(
                    "agent_skill_version_missing", "Skill 内容版本不存在，请重新导入", 409
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
            if "enabled" in values:
                setting = self._write_settings(
                    owner,
                    str(row.id),
                    row=row,
                    enabled=values.get("enabled"),
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
                raise Conflict("Skill 启停设置已更新，请核对最新设置")
            if values.enabled:
                current = self._builtin(key, owner) if row is None else self._detail(row)
                if not current.compatible:
                    raise WorkflowError(
                        "agent_skill_incompatible", "此 Skill 暂不可用，请先更新文件", 422
                    )
            self._write_settings(owner, key, row=row, enabled=values.enabled)
            self.session.flush()
            return self._builtin(key, owner) if row is None else self._detail(row)

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
            if item.kind == "image":
                with self._transaction(read_only=True):
                    version = current_version(self.session, str(identifier), values.content_version)
                    manifest = next(row for row in version.resources if row["path"] == item.path)
                resources.append(
                    dict(
                        path=item.path,
                        kind=item.kind,
                        encoding="base64",
                        content=base64.b64encode(self._resource_data(manifest)).decode(),
                    )
                )
                continue
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
