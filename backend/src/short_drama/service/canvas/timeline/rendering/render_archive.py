"""固定渲染上传身份；每次分片/完成都在同一事务内检查作者与执行租约。"""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.identity import ActorContext
from short_drama.domain import CanvasRenderJob, CanvasResourceUpload, MediaFile, ProjectCanvas
from short_drama.domain.identity.accounts.user import User
from short_drama.schemas.canvas.resources.resource import CanvasResourceRead, CanvasResourceStart
from short_drama.service.base import utcnow
from short_drama.service.canvas.resources.resource import CHUNK_SIZE, CanvasResourceService
from short_drama.service.canvas.timeline.timeline_renderer import RenderedTimeline
from short_drama.service.creation.assembly.rendering.renderer import RenderCancelled
from short_drama.storage.minio import MinioStorage

FILE_NAME = "timeline-render.mp4"


def archive_key(job_id: int) -> str:
    return f"canvas-render:{job_id}:0"


class CanvasRenderArchiveService(CanvasResourceService):
    def __init__(
        self,
        session: Session,
        settings: Settings,
        storage: MinioStorage,
        job: CanvasRenderJob,
        token: str,
    ) -> None:
        super().__init__(session, settings, storage)
        self.job_id, self.project_id, self.canvas_id = job.id, job.project_id, job.canvas_id
        self.token = token
        # 此身份只由可信Worker从持久任务构造；不接受HTTP身份或开启系统请求会话。
        session.info["actor"] = ActorContext(job.user_id, "", "", True, 0, "", "canvas-render")
        session.info["request_project"] = job.project_id

    def _lease(self) -> ProjectCanvas:
        canvas = self.canvases.require_canvas(self.project_id, self.canvas_id, lock=True)
        job = self.session.scalar(
            select(CanvasRenderJob)
            .where(CanvasRenderJob.id == self.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        user = self.session.scalar(
            select(User)
            .where(User.id == self.actor_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            job is None
            or job.status != "running"
            or job.lease_token != self.token
            or job.cancel_requested
            or (job.user_id, job.project_id, job.canvas_id)
            != (self.actor_id, self.project_id, self.canvas_id)
            or user is None
            or user.status != "active"
            or not user.email_verified_at
        ):
            raise RenderCancelled()
        return canvas

    def _scope(self, canvas_key: str) -> tuple[None, int, int]:
        scope = super()._scope(canvas_key)
        self._lease()
        if scope != (None, self.project_id, self.canvas_id):
            raise RenderCancelled()
        return scope

    def _upload(self, identifier: int, *, mode: str | None = None) -> CanvasResourceUpload:
        # 父项目/画布/任务先于上传锁，与API取消及权限变更保持同一顺序。
        self._lease()
        upload = super()._upload(identifier, mode=mode)
        self._identity(upload)
        return upload

    def _identity(self, upload: CanvasResourceUpload) -> None:
        if (
            upload.user_id != self.actor_id
            or upload.project_id != self.project_id
            or upload.canvas_id != self.canvas_id
            or upload.idempotency_hash
            != hashlib.sha256(archive_key(self.job_id).encode()).hexdigest()
            or upload.mode != "chunked"
            or upload.declared_json.get("kind") != "video"
            or upload.declared_json.get("file_name") != FILE_NAME
        ):
            raise RuntimeError("时间线成片归档身份校验失败")

    def recover(self) -> CanvasResourceRead | None:
        """先恢复已归档或已完整提交的分片；不因丢失回执再次编码。"""
        with self._transaction():
            self._lease()
            upload = self.resources.by_key(
                self.actor_id, hashlib.sha256(archive_key(self.job_id).encode()).hexdigest()
            )
            if upload is None:
                return None
            self._identity(upload)
            count = (upload.byte_size + CHUNK_SIZE - 1) // CHUNK_SIZE
            chunks = self.resources.chunks(upload.id)
            complete = len(chunks) == count and all(
                part.chunk_index == index for index, part in enumerate(chunks)
            )
            identifier = upload.id
            ready = upload.status == "ready"
            if not ready:
                upload.expires_at = utcnow() + timedelta(minutes=90)
        return self.complete(identifier) if ready or complete else None

    def store(
        self,
        rendered: RenderedTimeline,
        canvas_key: str,
        progress: Callable[..., None],
    ) -> CanvasResourceRead:
        request = CanvasResourceStart(
            file_name=FILE_NAME,
            kind="video",
            size=rendered.size,
            width=rendered.width,
            height=rendered.height,
            duration_ms=rendered.duration_ms,
        )
        progress("写入资源…", 92, True)
        upload = self.start(request, archive_key(self.job_id), canvas_key)
        with rendered.path.open("rb") as stream:
            for index in range(upload["chunk_count"]):
                progress("写入资源…", 94, True)
                self.put_chunk(upload["upload_id"], index, stream.read(CHUNK_SIZE))
        progress("写入资源…", 96, True)
        return self.complete(upload["upload_id"])

    def snapshot(self, resource_id: str) -> dict[str, Any]:
        with self._transaction(read_only=True):
            self._lease()
            upload = self.resources.by_key(
                self.actor_id, hashlib.sha256(archive_key(self.job_id).encode()).hexdigest()
            )
            if upload is None or upload.status != "ready" or upload.media_id != int(resource_id):
                raise RuntimeError("时间线成片归档回执校验失败")
            self._identity(upload)
            media = self.resources.resource(upload.media_id, lock=True)
            if (
                not isinstance(media, MediaFile)
                or media.created_by != self.actor_id
                or media.project_id != self.project_id
                or media.storage_locator != upload.storage_locator
                or media.format_code != "video/mp4"
            ):
                raise RuntimeError("时间线成片媒体归属校验失败")
            return {
                "media_id": str(media.id),
                "locator": media.storage_locator,
                "checksum": media.checksum_sha256,
                "size": media.byte_size,
                "width": media.width,
                "height": media.height,
                "duration_ms": media.duration_ms,
            }
