from __future__ import annotations

import base64
import hashlib
import io
from zipfile import ZIP_DEFLATED, ZipFile

from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.skill_catalog import BUILTIN_NAMES as BUILTIN_NAMES
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.skills.catalog.skill import (
    MAX_RESOURCE_BYTES,
    SkillMetadata,
    SkillResourceContent,
    resource_path,
)
from short_drama.service.skills.catalog.base import SkillServiceBase
from short_drama.service.skills.catalog.versions import (
    current_version,
    read_resource_manifest,
)
from short_drama.service.skills.packages.skill_package import (
    package_markdown,
)
from short_drama.service.skills.resources.skill_resource import (
    stage_object_key,
)


class SkillResources(SkillServiceBase):
    def _resource_data(self, item):
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
        return data

    def _resource_bytes(self, item):
        try:
            return self._resource_data(item).decode("utf-8")
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
            data = resource.resource_bytes()
            digest = hashlib.sha256(data).hexdigest()
            try:
                stored = staged.get(digest)
                if stored is None:
                    stored = self.storage.put(
                        self.settings.minio_image_bucket,
                        stage_object_key(owner, digest),
                        io.BytesIO(data),
                        len(data),
                        "application/octet-stream"
                        if resource.kind == "image"
                        else "text/plain; charset=utf-8",
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
            self._resource_data(item)
            staged[digest] = stored
            manifest.append(item)
        return manifest

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
            read_resource_manifest(item)
        if item["kind"] == "image":
            from short_drama.agent.resources.resolver import skill_resource_ref

            return SkillResourceContent(
                skill_id=str(identifier),
                content_version=number,
                **{key: item[key] for key in ("path", "kind", "byte_size", "checksum_sha256")},
                offset=0,
                content=base64.b64encode(self._resource_data(item)).decode(),
                encoding="base64",
                next_offset=None,
                resource_ref=skill_resource_ref(
                    item, f"skill:{identifier}:{number}:{path}"
                ).model_dump(mode="json"),
            )
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
            dict(path=item["path"], content=self._resource_data(item)) for item in manifest
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
