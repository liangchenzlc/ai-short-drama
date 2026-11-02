"""Explicit authorized references cross the scope filesystem boundary."""

from __future__ import annotations

import hashlib
import io
import mimetypes
import re
import shlex
import subprocess
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from short_drama.agent.context.media import inspect_attachment
from short_drama.agent.core.events import append_event
from short_drama.agent.resources.catalog import authorized_reference_locked
from short_drama.agent.resources.contracts import ResourceRef
from short_drama.agent.resources.resolver import ResourceResolver, skill_resource_ref
from short_drama.agent.skill.runtime import resource_manifest_locked
from short_drama.agent.tools.runtime import execution_locked
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.service.media.files.files import MediaFileService
from short_drama.service.media.files.storage import StorageService
from short_drama.service.media.files.upload_cleanup import cleanup_unreferenced_uploads
from short_drama.storage.minio import MinioStorage
from short_drama.utils.json_digest import digest

if TYPE_CHECKING:
    from langchain_opensandbox import OpenSandboxBackend

    from short_drama.agent.runtime.context import RuntimeContext

MAX_FILE_BYTES = 4 * 1024**2


def artifact_mime(path: str) -> str:
    canonical = {
        ".csv": "text/csv",
        ".json": "application/json",
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".log": "text/plain",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".sh": "text/x-shellscript",
    }
    return (
        canonical.get(PurePosixPath(path).suffix.lower())
        or mimetypes.guess_type(path)[0]
        or "application/octet-stream"
    )


def workspace_path(path: str) -> str:
    parts = path.split("/")
    if (
        not path
        or len(path) > 1024
        or "\\" in path
        or ":" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(path).is_absolute()
    ):
        raise WorkflowError("agent_sandbox_path", "只接受当前工作区内的相对文件路径", 422)
    return "/workspace/" + path


def skill_path(scope_id: str, activation_id: str, path: str, checksum: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", activation_id):
        raise WorkflowError("agent_sandbox_activation", "方法激活身份无效", 422)
    workspace_path(path)
    return f".skills/{scope_id}/{activation_id}/{checksum}/{path}"


class ImportEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: ResourceRef
    path: str

    @field_validator("path")
    @classmethod
    def relative(cls, value: str) -> str:
        workspace_path(value)
        return value


class ImportResources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resources: list[ImportEntry] = Field(min_length=1, max_length=32)


class ExportArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(min_length=1, max_length=32)

    @field_validator("paths")
    @classmethod
    def relative(cls, values: list[str]) -> list[str]:
        for value in values:
            workspace_path(value)
        return values


class MaterializeSkillResource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activation_id: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=1024)

    @field_validator("path")
    @classmethod
    def relative(cls, value: str) -> str:
        workspace_path(value)
        return value


def _checked_path(backend: OpenSandboxBackend, relative: str, *, existing: bool) -> str:
    absolute = workspace_path(relative)
    script = (
        "import pathlib; root=pathlib.Path('/workspace').resolve(); "
        f"target=pathlib.Path({absolute!r}); resolved=target.resolve(); "
        "assert resolved.is_relative_to(root) and resolved != root; "
        + (
            f"assert target.is_file() and target.stat().st_size <= {MAX_FILE_BYTES}; "
            if existing
            else ""
        )
        + "print(str(resolved))"
    )
    checked = backend.execute("python3 -c " + shlex.quote(script))
    if checked.exit_code != 0:
        raise WorkflowError("agent_sandbox_path", "文件不在当前工作区内或超过 4 MiB", 422)
    return checked.output.strip()


def _upload(backend: OpenSandboxBackend, relative: str, data: bytes, checksum: str) -> str:
    path = _checked_path(backend, relative, existing=False)
    probe = backend.execute("test -e " + shlex.quote(path))
    if probe.exit_code == 0:
        current = backend.download_files([path])[0]
        if current.error or hashlib.sha256(current.content).hexdigest() != checksum:
            raise Conflict("目标文件已被修改，请另存到新路径，不能覆盖")
        return path
    parent = str(PurePosixPath(path).parent)
    if backend.execute("mkdir -p -- " + shlex.quote(parent)).exit_code != 0:
        raise WorkflowError("agent_sandbox_import", "创建导入目录失败", 503)
    response = backend.upload_files([(path, data)])[0]
    if response.error:
        raise WorkflowError("agent_sandbox_import", "资源导入失败，请用原调用重试", 503)
    return path


class SandboxResourceService:
    def __init__(
        self, context: RuntimeContext, tool_call_id: str, *, state: dict | None = None
    ) -> None:
        self.context, self.tool_call_id, self.state = context, tool_call_id, state

    def import_resources(self, scope_id: int, refs: list[ImportEntry]) -> dict:
        if scope_id != self.context.scope_id:
            raise WorkflowError("agent_sandbox_scope", "资源不属于当前执行范围", 403)
        with self.context.factory.begin() as session:
            conversation, task, _, _ = execution_locked(session, self.context, self.tool_call_id)
            authorized = [
                authorized_reference_locked(session, conversation, task, entry.resource)
                for entry in refs
            ]
        storage = MinioStorage(self.context.settings)
        try:
            resolver = ResourceResolver(StorageService(storage, self.context.settings))
            payloads = [resolver.read_bytes(reference, MAX_FILE_BYTES) for reference in authorized]
        finally:
            storage.close()
        backend = self.context.sandbox.backend(scope_id, fence=self.context.fence)
        with self.context.sandbox.lock(scope_id):
            self._authorize_import(refs)
            imported = [
                {
                    "resource_ref": reference.model_dump(mode="json"),
                    "path": _upload(backend, entry.path, data, reference.checksum),
                }
                for entry, reference, data in zip(refs, authorized, payloads, strict=True)
            ]
        return {"imports": imported}

    def _authorize_import(self, refs: list[ImportEntry]) -> None:
        with self.context.factory.begin() as session:
            conversation, task, _, _ = execution_locked(session, self.context, self.tool_call_id)
            for entry in refs:
                authorized_reference_locked(session, conversation, task, entry.resource)

    def export_artifacts(self, scope_id: int, manifest: list[str]) -> dict:
        if scope_id != self.context.scope_id:
            raise WorkflowError("agent_sandbox_scope", "资源不属于当前执行范围", 403)
        backend = self.context.sandbox.backend(scope_id, fence=self.context.fence)
        references = []
        with self.context.sandbox.lock(scope_id):
            for relative in manifest:
                path = _checked_path(backend, relative, existing=True)
                response = backend.download_files([path])[0]
                if response.error:
                    raise WorkflowError("agent_sandbox_export", "无法读取待归档文件", 422)
                data = response.content
                mime = artifact_mime(relative)
                if mime.startswith(("image/", "video/", "audio/")):
                    references.append(
                        publish_media(self.context, self.tool_call_id, relative, data)
                    )
                else:
                    references.append(
                        publish_file(self.context, self.tool_call_id, relative, data, mime)
                    )
        return {"resources": references, "result_refs": references}

    def materialize_skill_resource(self, values: MaterializeSkillResource) -> dict:
        with self.context.factory.begin() as session:
            conversation, task, _, operation = execution_locked(
                session, self.context, self.tool_call_id
            )
            manifest = resource_manifest_locked(
                session, conversation, task, operation, values, state=self.state
            )
        storage = MinioStorage(self.context.settings)
        try:
            reference = skill_resource_ref(
                manifest, f"skill-resource:{values.activation_id}:{values.path}"
            )
            data = ResourceResolver(StorageService(storage, self.context.settings)).read_bytes(
                reference, MAX_FILE_BYTES
            )
        finally:
            storage.close()
        relative = skill_path(
            str(self.context.scope_id), values.activation_id, values.path, reference.checksum
        )
        backend = self.context.sandbox.backend(self.context.scope_id, fence=self.context.fence)
        with self.context.sandbox.lock(self.context.scope_id):
            with self.context.factory.begin() as session:
                conversation, task, _, operation = execution_locked(
                    session, self.context, self.tool_call_id
                )
                if (
                    resource_manifest_locked(
                        session, conversation, task, operation, values, state=self.state
                    )
                    != manifest
                ):
                    raise Conflict("冻结方法资源已变化")
            path = _upload(backend, relative, data, reference.checksum)
        return {
            "activation_id": values.activation_id,
            "path": path,
            "resource_ref": reference.model_dump(mode="json"),
        }


def publish_media(context: RuntimeContext, call_id: str, relative: str, data: bytes) -> dict:
    checksum = hashlib.sha256(data).hexdigest()
    with context.factory.begin() as session:
        conversation, task, _, operation = execution_locked(session, context, call_id)
        ref_id = "sandbox:" + digest([operation.id, relative, checksum])
        existing = next((item for item in operation.result_refs if item["ref_id"] == ref_id), None)
        if existing:
            return authorized_reference_locked(
                session, conversation, task, ResourceRef.model_validate(existing)
            ).model_dump(mode="json")
    name = PurePosixPath(relative).name[:255]
    try:
        kind, mime, _, metadata = inspect_attachment(data, name, context.settings)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise WorkflowError("agent_sandbox_media", f"无法归档媒体：{error}", 422) from None
    client = MinioStorage(context.settings)
    storage = StorageService(client, context.settings)
    stored = None
    try:
        stored = storage.upload(io.BytesIO(data), length=len(data), content_type=mime)
        with context.factory.begin() as session:
            conversation, task, run, operation = execution_locked(session, context, call_id)
            existing = next(
                (item for item in operation.result_refs if item["ref_id"] == ref_id), None
            )
            if existing:
                return authorized_reference_locked(
                    session, conversation, task, ResourceRef.model_validate(existing)
                ).model_dump(mode="json")
            media = MediaFileService(session).record_upload_locked(
                stored, name=name, checksum=checksum, metadata=metadata
            )
            reference = ResourceRef(
                kind=kind,
                ref_id=ref_id,
                uri=media.storage_locator,
                source="sandbox_artifact",
                checksum=checksum,
                metadata={
                    "path": relative,
                    "name": name,
                    "media_id": str(media.id),
                    "mime_type": mime,
                    "byte_size": len(data),
                    "operation_id": str(operation.id),
                    "scope_id": str(context.scope_id),
                },
            ).model_dump(mode="json")
            operation.result_refs = [*operation.result_refs, reference]
            _artifact_event(session, conversation, run, context, operation, reference)
        return reference
    finally:
        if stored is not None:
            # Also safe when COMMIT succeeded but its acknowledgement was lost.
            with context.factory() as session:
                cleanup_unreferenced_uploads(session, storage, [stored])
        client.close()


def _artifact_event(session, conversation, run, context, operation, reference):
    append_event(
        session,
        conversation,
        "sandbox.artifact",
        {
            "scope_id": str(context.scope_id),
            "operation_id": str(operation.id),
            "resource_ref": reference,
        },
        run_id=run.id,
    )


def publish_file(
    context: RuntimeContext,
    call_id: str,
    relative: str,
    data: bytes,
    mime: str,
    *,
    source: str = "sandbox_artifact",
) -> dict:
    storage = MinioStorage(context.settings)
    service = StorageService(storage, context.settings)
    try:
        with context.factory.begin() as session:
            conversation, _, _, operation = execution_locked(session, context, call_id)
            invocation = f"operation:{operation.id}"
            location = service.agent_resource_location(
                data, invocation_id=invocation, content_type=mime
            )
            reference = ResourceRef(
                kind="script"
                if relative.endswith((".py", ".js", ".mjs", ".sh"))
                else "text"
                if mime.startswith("text/") or mime == "application/json"
                else "file",
                ref_id="sandbox:"
                + digest([operation.id, relative, hashlib.sha256(data).hexdigest()]),
                uri=location.locator,
                source=source,
                checksum=hashlib.sha256(data).hexdigest(),
                availability="unavailable",
                metadata={
                    "path": relative,
                    "mime_type": mime,
                    "byte_size": len(data),
                    "operation_id": str(operation.id),
                    "scope_id": str(context.scope_id),
                },
            )
            # 先保留私有对象的回收身份，上传和最终确认均不持数据库锁。
            refs = [
                item for item in operation.result_refs if item.get("ref_id") != reference.ref_id
            ]
            operation.result_refs = [*refs, reference.model_dump(mode="json")]
        service.freeze_agent_resource(data, invocation_id=invocation, content_type=mime)
        reference = reference.model_copy(update={"availability": "available"})
        with context.factory.begin() as session:
            conversation, _, run, operation = execution_locked(session, context, call_id)
            operation.result_refs = [
                reference.model_dump(mode="json")
                if item.get("ref_id") == reference.ref_id
                else item
                for item in operation.result_refs
            ]
            _artifact_event(
                session, conversation, run, context, operation, reference.model_dump(mode="json")
            )
        return reference.model_dump(mode="json")
    finally:
        storage.close()
