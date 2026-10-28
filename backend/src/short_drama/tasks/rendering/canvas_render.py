"""私人画布成片的可靠发布、租约执行与固定媒体归档。"""

import hashlib
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError
from short_drama.domain import CanvasRenderJob, MediaFile, Project, ProjectCanvas
from short_drama.schemas.canvas.resources.resource import CanvasResourceRead
from short_drama.schemas.canvas.timeline.timeline import CanvasTimelinePlanRead
from short_drama.service.base import utcnow
from short_drama.service.canvas.timeline.rendering.render_archive import (
    FILE_NAME,
    CanvasRenderArchiveService,
)
from short_drama.service.canvas.timeline.timeline_native import output_duration_within_plan
from short_drama.service.canvas.timeline.timeline_renderer import CanvasTimelineRenderer
from short_drama.service.creation.assembly.rendering.renderer import RenderCancelled
from short_drama.service.generations.runtime.access import may_submit
from short_drama.service.media.files.storage import StorageService
from short_drama.storage.minio import MinioStorage
from short_drama.tasks.celery_app import make_celery, render_queue

logger = logging.getLogger(__name__)
LEASE_SECONDS = 120


def locked_job(
    session: Session,
    identifier: int,
) -> tuple[CanvasRenderJob | None, ProjectCanvas | None]:
    scope = session.execute(
        select(CanvasRenderJob.project_id, CanvasRenderJob.canvas_id).where(
            CanvasRenderJob.id == identifier
        )
    ).first()
    if scope is None:
        return None, None
    session.scalar(select(Project).where(Project.id == scope.project_id).with_for_update())
    canvas = session.scalar(
        select(ProjectCanvas).where(ProjectCanvas.id == scope.canvas_id).with_for_update()
    )
    job = session.scalar(
        select(CanvasRenderJob)
        .where(CanvasRenderJob.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return job, canvas


def allowed(session: Session, job: CanvasRenderJob, canvas: ProjectCanvas | None) -> bool:
    return (
        canvas is not None
        and canvas.archived_at is None
        and may_submit(
            session,
            SimpleNamespace(
                id=job.id,
                initiated_by=job.user_id,
                project_id=job.project_id,
                scope_user_id=None,
            ),
        )
    )


def finish_state(job: CanvasRenderJob, status: str, stage: str) -> None:
    job.status, job.stage, job.finished_at = status, stage, utcnow()
    job.lease_token, job.locked_until = None, None
    job.updated_at = utcnow()


class CanvasRenderPublisher:
    def __init__(self, factory: sessionmaker[Session], settings: Settings) -> None:
        self.factory, self.settings = factory, settings
        self.app = make_celery(settings)

    def tick(self) -> bool:
        now = utcnow()
        with self.factory.begin() as session:
            expired = session.scalars(
                select(CanvasRenderJob)
                .where(
                    CanvasRenderJob.status == "running",
                    CanvasRenderJob.locked_until < now,
                )
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            for job in expired:
                job.lease_token, job.locked_until = None, None
                job.message_version += 1
                job.next_run_at, job.updated_at = now, now
                if job.cancel_requested:
                    finish_state(job, "cancelled", "已取消")
                elif job.attempts >= 3:
                    finish_state(job, "failed", "渲染失败")
                    job.error_json = {
                        "code": "canvas_render_interrupted",
                        "message": "时间线渲染服务连续中断，请检查后明确恢复",
                    }
                else:
                    job.status, job.stage, job.progress = "queued", "排队中", 5
            # autoflush关闭，先落下恢复状态再寻找本次可发布的任务。
            session.flush()
            job = session.scalar(
                select(CanvasRenderJob)
                .where(
                    CanvasRenderJob.status == "queued",
                    CanvasRenderJob.next_run_at <= now,
                )
                .order_by(CanvasRenderJob.next_run_at, CanvasRenderJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return False
            job.next_run_at = now + timedelta(seconds=15)
            identifier, version = str(job.id), str(job.message_version)
        queue = render_queue(self.settings)
        try:
            self.app.send_task(
                "short_drama.execute_canvas_render",
                args=[identifier, version],
                queue=queue.name,
                routing_key="render",
                retry=False,
            )
        except Exception as error:
            logger.warning(
                "Canvas render publication deferred: job=%s error=%s",
                identifier,
                type(error).__name__,
            )
            raise RuntimeError("发布画布渲染任务失败，调度器将按原任务标识重试") from None
        return True


class VerifiedStream:
    def __init__(self, stream: BinaryIO, snapshot: dict[str, Any], maximum: int) -> None:
        self.stream, self.snapshot, self.maximum = stream, snapshot, maximum
        self.digest, self.size = hashlib.sha256(), 0
        self.checked = False

    def read(self, size: int) -> bytes:
        block = self.stream.read(size)
        if block:
            self.size += len(block)
            expected = self.snapshot.get("size")
            if self.size > self.maximum or (expected is not None and self.size > expected):
                raise RuntimeError("时间线媒体字节长度校验失败")
            self.digest.update(block)
        elif not self.checked:
            self.checked = True
            expected = self.snapshot.get("size")
            checksum = self.snapshot.get("checksum")
            if (expected is not None and self.size != expected) or (
                checksum and self.digest.hexdigest() != checksum
            ):
                raise RuntimeError("时间线媒体字节校验失败，来源与冻结快照不一致")
        return block


class CanvasRenderExecutor:
    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        storage: MinioStorage,
    ) -> None:
        self.factory, self.settings, self.storage = factory, settings, storage
        self.media_storage = StorageService(storage, settings)

    def _claim(
        self,
        identifier: int,
        version: int,
        token: str,
    ) -> tuple[CanvasRenderJob, str] | None:
        with self.factory.begin() as session:
            job, canvas = locked_job(session, identifier)
            if job is None or job.status != "queued" or job.message_version != version:
                return None
            if job.cancel_requested or not allowed(session, job, canvas):
                finish_state(job, "cancelled", "已取消")
                return None
            job.status, job.stage, job.progress = "running", "准备媒体…", 10
            job.attempts += 1
            job.started_at = job.started_at or utcnow()
            job.lease_token = token
            job.locked_until = utcnow() + timedelta(seconds=LEASE_SECONDS)
            job.updated_at = utcnow()
            return job, canvas.source_key

    def _heartbeat(self, identifier: int, token: str, stage: str, progress: int) -> None:
        with self.factory.begin() as session:
            job, canvas = locked_job(session, identifier)
            if (
                job is None
                or job.status != "running"
                or job.lease_token != token
                or job.cancel_requested
                or not allowed(session, job, canvas)
            ):
                raise RenderCancelled()
            job.stage, job.progress = stage, max(job.progress, min(progress, 99))
            job.locked_until = utcnow() + timedelta(seconds=LEASE_SECONDS)
            job.updated_at = utcnow()

    @contextmanager
    def _source(self, snapshot: dict[str, Any], maximum: int) -> Iterator[VerifiedStream]:
        try:
            with self.media_storage.open(snapshot["locator"]) as stream:
                yield VerifiedStream(stream, snapshot, maximum)
        except BusinessError as error:
            raise RuntimeError("读取时间线媒体失败，请检查存储后恢复") from error

    def _verify(
        self,
        archive: CanvasRenderArchiveService,
        resource: CanvasResourceRead,
        plan: CanvasTimelinePlanRead,
        heartbeat: Callable[..., None],
    ) -> dict[str, Any]:
        snapshot = archive.snapshot(resource.id)
        heartbeat("检查成片…", 97, True)
        root = Path(self.settings.render_scratch_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="canvas-timeline-verify-", dir=root) as directory:
            path = Path(directory) / "render-output.mp4"
            with (
                self._source(snapshot, self.settings.render_max_scratch_bytes) as source,
                path.open("wb") as file,
            ):
                while block := source.read(1024 * 1024):
                    heartbeat("检查成片…", 97)
                    file.write(block)
            probed = CanvasTimelineRenderer(self.settings).probe(path)
            if (
                not probed.facts.has_video
                or not probed.facts.has_audio
                or (probed.width, probed.height) != (plan.output.width, plan.output.height)
                or (snapshot["width"], snapshot["height"]) != (probed.width, probed.height)
                or not output_duration_within_plan(
                    plan.duration_ms, probed.facts.duration_ms, plan.output.fps
                )
                or abs((snapshot["duration_ms"] or 0) - probed.facts.duration_ms) > 1
            ):
                raise RuntimeError("时间线已归档成片轨道、时长或尺寸校验失败")
        return snapshot

    def _finish(
        self,
        identifier: int,
        token: str,
        snapshot: dict[str, Any],
        plan: CanvasTimelinePlanRead,
    ) -> None:
        with self.factory.begin() as session:
            job, canvas = locked_job(session, identifier)
            if (
                job is None
                or job.status != "running"
                or job.lease_token != token
                or job.cancel_requested
                or not allowed(session, job, canvas)
            ):
                raise RenderCancelled()
            media = session.scalar(
                select(MediaFile).where(MediaFile.id == int(snapshot["media_id"])).with_for_update()
            )
            if (
                media is None
                or media.created_by != job.user_id
                or media.project_id != job.project_id
                or media.storage_locator != snapshot["locator"]
                or media.checksum_sha256 != snapshot["checksum"]
                or media.byte_size != snapshot["size"]
            ):
                raise RuntimeError("写入时间线完成回执失败，归档媒体已变化")
            job.output_media_id = media.id
            job.result_json = {
                "resourceId": str(media.id),
                "fileName": FILE_NAME,
                "size": media.byte_size,
                "durationMs": media.duration_ms,
                **({"subtitleSrt": plan.subtitle_srt} if plan.subtitle_srt else {}),
            }
            job.error_json = None
            job.progress = 100
            finish_state(job, "succeeded", "渲染完成")

    def _fail(self, identifier: int, token: str, error: Exception) -> None:
        # 数据库写入失败须传播给Celery；不能确认一条没有持久终态的消息。
        with self.factory.begin() as session:
            job, canvas = locked_job(session, identifier)
            if job is None or job.status != "running" or job.lease_token != token:
                return
            cancelled = (
                isinstance(error, RenderCancelled)
                or job.cancel_requested
                or not allowed(session, job, canvas)
            )
            finish_state(
                job, "cancelled" if cancelled else "failed", "已取消" if cancelled else "渲染失败"
            )
            if not cancelled:
                job.error_json = {
                    "code": "canvas_render_failed",
                    "message": str(error)
                    if isinstance(error, RuntimeError)
                    else "时间线渲染未完成，请检查编码器、数据库和存储后恢复",
                }

    def execute(self, job_id: str, message_version: str) -> None:
        identifier, version, token = int(job_id), int(message_version), uuid4().hex
        claimed = self._claim(identifier, version, token)
        if claimed is None:
            return
        job, canvas_key = claimed
        last_beat = 0.0

        def heartbeat(stage, progress, force=False):
            nonlocal last_beat
            if force or time.monotonic() - last_beat >= 1:
                self._heartbeat(identifier, token, stage, progress)
                last_beat = time.monotonic()

        try:
            plan = CanvasTimelinePlanRead.model_validate(job.plan_json)
            with self.factory() as session:
                archive = CanvasRenderArchiveService(
                    session, self.settings, self.storage, job, token
                )
                heartbeat("检查归档…", 10, True)
                resource = archive.recover()
                if resource is None:

                    def open_source(source_id):
                        source = job.source_snapshot.get(source_id)
                        if not isinstance(source, dict):
                            raise RuntimeError("时间线缺少已授权的冻结媒体来源")
                        return self._source(source, self.settings.render_max_source_bytes)

                    with CanvasTimelineRenderer(self.settings, heartbeat).render(
                        plan, open_source
                    ) as rendered:
                        resource = archive.store(rendered, canvas_key, heartbeat)
                snapshot = self._verify(archive, resource, plan, heartbeat)
            heartbeat("写入资源…", 99, True)
            self._finish(identifier, token, snapshot, plan)
        except Exception as error:
            logger.warning(
                "Canvas timeline render interrupted: job=%s error=%s",
                identifier,
                type(error).__name__,
            )
            self._fail(identifier, token, error)
