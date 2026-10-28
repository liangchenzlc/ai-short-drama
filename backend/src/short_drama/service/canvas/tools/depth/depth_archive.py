"""深度结果以 depth:<task-id> 永久归档；完成回执未知时复用原上传身份。"""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.identity import ActorContext
from short_drama.domain import CanvasLocalJob, CanvasResourceUpload, MediaFile
from short_drama.domain.identity.accounts.user import User
from short_drama.schemas.canvas.resources.resource import CanvasResourceRead, CanvasResourceStart
from short_drama.service.base import utcnow
from short_drama.service.canvas.resources.resource import CHUNK_SIZE, CanvasResourceService
from short_drama.service.canvas.tools.depth.depth_execute import CapturedDepth
from short_drama.service.creation.assembly.rendering.renderer import RenderCancelled
from short_drama.storage.minio import MinioStorage

FILE_NAME = "depth-action-reference.mp4"


def archive_key(job_id: int) -> str:
    return f"depth:{job_id}"


class CanvasDepthArchiveService(CanvasResourceService):
    def __init__(
        self,
        session: Session,
        settings: Settings,
        storage: MinioStorage,
        job: CanvasLocalJob,
        token: str,
    ) -> None:
        super().__init__(session, settings, storage)
        self.job_id, self.project_id, self.canvas_id = job.id, job.project_id, job.canvas_id
        self.token = token
        session.info["actor"] = ActorContext(job.user_id, "", "", True, 0, "", "canvas-depth")
        session.info["request_project"] = job.project_id

    def _lease(self) -> None:
        if self.canvas_id is not None:
            self.canvases.require_canvas(self.project_id, self.canvas_id, lock=True)
        job = self.session.scalar(
            select(CanvasLocalJob)
            .where(CanvasLocalJob.id == self.job_id)
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
            or job.task_type != "depth_capture"
            or job.status != "running"
            or job.lease_token != self.token
            or job.cancel_requested
            or job.locked_until is None
            or job.locked_until <= utcnow()
            or (job.user_id, job.project_id, job.canvas_id)
            != (self.actor_id, self.project_id, self.canvas_id)
            or user is None
            or user.status != "active"
            or not user.email_verified_at
        ):
            raise RenderCancelled()
        source = self.session.scalar(
            select(MediaFile).where(MediaFile.id == job.source_media_id).with_for_update(read=True)
        )
        if source is None or not (
            (source.scope_user_id == self.actor_id and source.project_id is None)
            or (source.project_id == self.project_id and self.project_id is not None)
        ):
            raise RenderCancelled()

    def _scope(self, canvas_key: str | None) -> tuple[int | None, int | None, int | None]:
        scope = super()._scope(canvas_key)
        self._lease()
        expected = (
            (None, self.project_id, self.canvas_id)
            if self.canvas_id is not None
            else (self.actor_id, None, None)
        )
        if scope != expected:
            raise RenderCancelled()
        return scope

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
            raise RuntimeError("深度视频归档身份校验失败")

    def _upload(self, identifier: int, *, mode: str | None = None) -> CanvasResourceUpload:
        self._lease()
        upload = super()._upload(identifier, mode=mode)
        self._identity(upload)
        return upload

    def recover(self) -> CanvasResourceRead | None:
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
            identifier, ready = upload.id, upload.status == "ready"
            if not ready:
                upload.expires_at = utcnow() + timedelta(minutes=90)
        return self.complete(identifier) if ready or complete else None

    def store(
        self,
        captured: CapturedDepth,
        canvas_key: str | None,
        progress: Callable[..., None],
    ) -> CanvasResourceRead:
        request = CanvasResourceStart(
            file_name=FILE_NAME,
            kind="video",
            size=captured.size,
            width=1920,
            height=1080,
            duration_ms=captured.duration_ms,
        )
        progress("保存到素材库", 92, True)
        upload = self.start(request, archive_key(self.job_id), canvas_key)
        with captured.path.open("rb") as stream:
            for index in range(upload["chunk_count"]):
                progress("保存到素材库", 94, True)
                self.put_chunk(upload["upload_id"], index, stream.read(CHUNK_SIZE))
        progress("保存到素材库", 96, True)
        return self.complete(upload["upload_id"])

    def snapshot(self, resource_id: str) -> dict[str, Any]:
        with self._transaction(read_only=True):
            self._lease()
            upload = self.resources.by_key(
                self.actor_id, hashlib.sha256(archive_key(self.job_id).encode()).hexdigest()
            )
            if upload is None or upload.status != "ready" or upload.media_id != int(resource_id):
                raise RuntimeError("深度视频归档回执校验失败")
            self._identity(upload)
            media = self.resources.resource(upload.media_id, lock=True)
            if (
                not isinstance(media, MediaFile)
                or media.created_by != self.actor_id
                or media.project_id != self.project_id
                or media.scope_user_id != (self.actor_id if self.project_id is None else None)
                or media.storage_locator != upload.storage_locator
                or media.format_code != "video/mp4"
                or (media.width, media.height) != (1920, 1080)
            ):
                raise RuntimeError("深度视频媒体归属或尺寸校验失败")
            return {
                "media_id": str(media.id),
                "locator": media.storage_locator,
                "checksum": media.checksum_sha256,
                "size": media.byte_size,
                "duration_ms": media.duration_ms,
                "width": media.width,
                "height": media.height,
            }
