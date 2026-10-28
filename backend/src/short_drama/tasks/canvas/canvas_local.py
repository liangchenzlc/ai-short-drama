"""私人本地媒体任务的可靠发布、租约和当前权限复核。"""

import hashlib
import logging
import time
from collections.abc import Iterator
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
from short_drama.dao.canvas.tools.local.local import CanvasLocalDAO
from short_drama.domain import CanvasLocalJob, MediaFile, Project, ProjectCanvas
from short_drama.schemas.canvas.tools.depth.depth import CanvasDepthCaptureResult
from short_drama.schemas.canvas.tools.transcription.transcription import CanvasTranscriptionResult
from short_drama.service.base import utcnow
from short_drama.service.canvas.timeline.playback.playback_cleanup import cleanup_canvas_playback
from short_drama.service.canvas.timeline.playback.playback_execute import compatible_video
from short_drama.service.canvas.timeline.timeline_renderer import CanvasTimelineRenderer
from short_drama.service.canvas.tools.depth.depth_archive import CanvasDepthArchiveService
from short_drama.service.canvas.tools.depth.depth_execute import CanvasDepthCaptureExecutor
from short_drama.service.canvas.tools.transcription.cloud_transcription import (
    CanvasCloudTranscriptionExecutor,
    require_cloud_transcription,
)
from short_drama.service.canvas.tools.transcription.transcription_execute import (
    CanvasTranscriptionExecutor,
)
from short_drama.service.creation.assembly.rendering.renderer import RenderCancelled
from short_drama.service.generations.runtime.access import may_submit
from short_drama.service.media.files.storage import StorageService
from short_drama.storage.minio import MinioStorage
from short_drama.storage.models import ObjectLocation
from short_drama.tasks.celery_app import make_celery, render_queue
from short_drama.utils.snowflake import next_id

logger = logging.getLogger(__name__)


def locked_job(
    session: Session, identifier: int
) -> tuple[CanvasLocalJob | None, ProjectCanvas | None]:
    scope = session.execute(
        select(CanvasLocalJob.project_id, CanvasLocalJob.canvas_id).where(
            CanvasLocalJob.id == identifier
        )
    ).first()
    if scope is None:
        return None, None
    canvas = None
    if scope.project_id is not None:
        session.scalar(select(Project).where(Project.id == scope.project_id).with_for_update())
        canvas = session.scalar(
            select(ProjectCanvas).where(ProjectCanvas.id == scope.canvas_id).with_for_update()
        )
    job = session.scalar(
        select(CanvasLocalJob)
        .where(CanvasLocalJob.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return job, canvas


def allowed(session: Session, job: CanvasLocalJob, canvas: ProjectCanvas | None) -> bool:
    return (
        job.canvas_id is None or (canvas is not None and canvas.archived_at is None)
    ) and may_submit(
        session,
        SimpleNamespace(
            id=job.id,
            initiated_by=job.user_id,
            project_id=job.project_id,
            scope_user_id=job.user_id if job.project_id is None else None,
        ),
    )


def finish_state(job: CanvasLocalJob, status: str, stage: str) -> None:
    job.status, job.stage, job.finished_at = status, stage, utcnow()
    job.lease_token, job.locked_until = None, None
    job.updated_at = utcnow()


class CanvasLocalPublisher:
    def __init__(self, factory: sessionmaker[Session], settings: Settings) -> None:
        self.factory, self.settings = factory, settings
        self.app = make_celery(settings)

    def tick(self) -> bool:
        now = utcnow()
        with self.factory.begin() as session:
            expired = session.scalars(
                select(CanvasLocalJob)
                .where(CanvasLocalJob.status == "running", CanvasLocalJob.locked_until < now)
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
                    finish_state(job, "failed", "本地任务中断")
                    job.error_json = {
                        "code": "canvas_local_interrupted",
                        "message": "本地媒体服务连续中断，请检查后明确恢复",
                    }
                else:
                    job.status, job.stage, job.progress = "queued", "排队中", 5
            session.flush()
            job = session.scalar(
                select(CanvasLocalJob)
                .where(CanvasLocalJob.status == "queued", CanvasLocalJob.next_run_at <= now)
                .order_by(CanvasLocalJob.next_run_at, CanvasLocalJob.id)
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
                "short_drama.execute_canvas_local",
                args=[identifier, version],
                queue=queue.name,
                routing_key="render",
                retry=False,
            )
        except Exception as error:
            logger.warning(
                "Canvas local publication deferred: job=%s error=%s",
                identifier,
                type(error).__name__,
            )
            raise RuntimeError("发布本地媒体任务失败，调度器将按原任务标识重试") from None
        return True


class VerifiedLocalSource:
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
                raise RuntimeError("待处理媒体字节长度校验失败")
            self.digest.update(block)
        elif not self.checked:
            self.checked = True
            expected = self.snapshot.get("size")
            checksum = self.snapshot.get("checksum")
            if (expected is not None and self.size != expected) or (
                checksum and self.digest.hexdigest() != checksum
            ):
                raise RuntimeError("待处理媒体字节校验失败，来源与冻结快照不一致")
        return block


class CanvasLocalExecutor:
    def __init__(
        self, factory: sessionmaker[Session], settings: Settings, storage: MinioStorage
    ) -> None:
        self.factory, self.settings, self.storage = factory, settings, storage
        self.media_storage = StorageService(storage, settings)

    def _claim(self, identifier: int, version: int, token: str) -> CanvasLocalJob | None:
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
            job.locked_until = utcnow() + timedelta(seconds=self.settings.generation_lease_seconds)
            job.updated_at = utcnow()
            return job

    def _current(self, session: Session, identifier: int, token: str) -> CanvasLocalJob:
        job, canvas = locked_job(session, identifier)
        if (
            job is None
            or job.status != "running"
            or job.lease_token != token
            or job.cancel_requested
            or job.locked_until is None
            or job.locked_until <= utcnow()
            or not allowed(session, job, canvas)
        ):
            raise RenderCancelled()
        source = session.scalar(
            select(MediaFile).where(MediaFile.id == job.source_media_id).with_for_update(read=True)
        )
        if source is None or not (
            (source.scope_user_id == job.user_id and source.project_id is None)
            or (
                source.project_id == job.project_id
                and job.project_id is not None
                and (source.published_at is not None or source.created_by == job.user_id)
            )
        ):
            raise RenderCancelled()
        return job

    def _heartbeat(self, identifier: int, token: str, stage: str, progress: int) -> None:
        with self.factory.begin() as session:
            job = self._current(session, identifier, token)
            job.stage, job.progress = stage, max(job.progress, min(progress, 99))
            job.locked_until = utcnow() + timedelta(seconds=self.settings.generation_lease_seconds)
            job.updated_at = utcnow()

    @contextmanager
    def _source(self, snapshot: dict[str, Any]) -> Iterator[VerifiedLocalSource]:
        try:
            with self.media_storage.open(snapshot["locator"]) as stream:
                yield VerifiedLocalSource(stream, snapshot, self.settings.render_max_source_bytes)
        except (BusinessError, OSError) as error:
            raise RuntimeError("读取待处理媒体失败，请检查存储后恢复") from error

    def _finish(self, identifier: int, token: str, result: CanvasTranscriptionResult) -> None:
        result = CanvasTranscriptionResult.model_validate(result.model_dump(by_alias=True))
        with self.factory.begin() as session:
            job = self._current(session, identifier, token)
            job.result_json = result.model_dump(mode="json", by_alias=True)
            job.progress = 100
            finish_state(job, "succeeded", "转写完成")

    def _fail(self, identifier: int, token: str, error: Exception) -> None:
        with self.factory.begin() as session:
            job, canvas = locked_job(session, identifier)
            if (
                job is None
                or job.status != "running"
                or job.lease_token != token
                or job.locked_until <= utcnow()
            ):
                return
            if (
                isinstance(error, RenderCancelled)
                or job.cancel_requested
                or not allowed(session, job, canvas)
            ):
                finish_state(job, "cancelled", "已取消")
            else:
                depth = job.task_type == "depth_capture"
                playback = job.task_type == "resource_playback"
                finish_state(
                    job,
                    "failed",
                    "兼容预览失败" if playback else "深度处理失败" if depth else "转写失败",
                )
                message = (
                    str(error)
                    if isinstance(error, (RuntimeError, BusinessError))
                    else "本地媒体处理失败，请检查媒体和服务后明确恢复"
                )
                job.error_json = {
                    "code": "canvas_playback_failed"
                    if playback
                    else "canvas_depth_failed"
                    if depth
                    else "canvas_transcription_failed",
                    "message": message[:2000],
                }
                if (job.result_json or {}).get("cloud_submission_started"):
                    job.error_json = {
                        "code": "canvas_transcription_submission_unknown",
                        "message": message[:1600]
                        + "；此请求可能已到达云端，未自动重发，请到渠道核实。",
                    }

    def _playback(self, job: CanvasLocalJob, token: str, heartbeat) -> None:
        with self._source(job.source_snapshot) as source:
            with compatible_video(source, self.settings, heartbeat) as (path, facts):
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                location = ObjectLocation(
                    self.settings.minio_video_bucket, f"canvas-playback/{job.id}/{token}.mp4"
                )
                heartbeat("保存兼容预览", 90, True)
                cleanup_canvas_playback(
                    self.factory, self.settings, self.storage, job_id=job.id, token=token
                )
                with self.factory.begin() as session:
                    current = self._current(session, job.id, token)
                    if (current.result_json or {}).get("playback_staging"):
                        raise RuntimeError("上次兼容预览文件尚未清理，请稍后重新读取")
                    current.result_json = {"playback_staging": [location.locator]}
                try:
                    with path.open("rb") as stream:
                        self.storage.put(
                            location.bucket,
                            location.object_name,
                            stream,
                            path.stat().st_size,
                            "video/mp4",
                        )
                    with self.factory.begin() as session:
                        current = self._current(session, job.id, token)
                        original = session.get(MediaFile, current.source_media_id)
                        media = MediaFile(
                            id=next_id(),
                            project_id=original.project_id,
                            scope_user_id=original.scope_user_id,
                            published_at=original.published_at,
                            format_code="video/mp4",
                            storage_locator=location.locator,
                            original_name="compatible-preview.mp4",
                            byte_size=path.stat().st_size,
                            width=facts["width"],
                            height=facts["height"],
                            duration_ms=facts["duration_ms"],
                            checksum_sha256=digest,
                            video_metadata=facts,
                            created_at=utcnow(),
                            updated_at=utcnow(),
                            created_by=job.user_id,
                            updated_by=job.user_id,
                        )
                        session.add(media)
                        session.flush()
                        current.output_media_id = media.id
                        current.result_json = {
                            "resourceId": str(media.id),
                            "sourceResourceId": str(original.id),
                            "profile": "h264-aac-v1",
                        }
                        current.progress = 100
                        finish_state(current, "succeeded", "兼容预览已就绪")

                except Exception:
                    cleanup_canvas_playback(
                        self.factory, self.settings, self.storage, job_id=job.id, token=token
                    )
                    raise

    def _depth(self, job: CanvasLocalJob, token: str, heartbeat) -> None:
        with self.factory() as session:
            archive = CanvasDepthArchiveService(session, self.settings, self.storage, job, token)
            heartbeat("检查归档…", 10, True)
            resource = archive.recover()
            receipt = job.result_json or {}
            if resource is None:
                with self._source(job.source_snapshot) as source:
                    with CanvasDepthCaptureExecutor(self.settings, heartbeat).capture(
                        source, job.source_snapshot
                    ) as captured:
                        receipt = {
                            "device": captured.device,
                            "fallbackReason": captured.fallback_reason,
                            "processingMs": captured.processing_ms,
                        }
                        with self.factory.begin() as authority:
                            self._current(authority, job.id, token).result_json = receipt
                        resource = archive.store(
                            captured,
                            self._canvas_key(job.canvas_id),
                            heartbeat,
                        )
            snapshot = archive.snapshot(resource.id)
        if receipt.get("device") not in {"cpu", "cuda", "mps"}:
            raise RuntimeError("深度视频归档缺少组件执行回执，不能猜测实际设备")
        self._verify_depth(snapshot, job.source_snapshot, heartbeat)
        heartbeat("检查深度视频…", 99, True)
        with self.factory.begin() as session:
            current = self._current(session, job.id, token)
            media = session.scalar(
                select(MediaFile).where(MediaFile.id == int(snapshot["media_id"])).with_for_update()
            )
            if (
                media is None
                or media.storage_locator != snapshot["locator"]
                or media.checksum_sha256 != snapshot["checksum"]
                or media.byte_size != snapshot["size"]
                or media.created_by != job.user_id
                or media.project_id != job.project_id
            ):
                raise RuntimeError("写入深度视频完成回执失败，归档媒体已变化")
            result = CanvasDepthCaptureResult(
                resource_id=str(media.id),
                size=media.byte_size,
                duration_ms=media.duration_ms,
                device=receipt["device"],
                fallback_reason=receipt.get("fallbackReason", ""),
                processing_ms=receipt.get("processingMs", 0),
            )
            current.output_media_id = media.id
            current.result_json = result.model_dump(mode="json", by_alias=True)
            current.progress, current.error_json = 100, None
            finish_state(current, "succeeded", "深度捕捉完成")

    def _verify_depth(self, snapshot: dict, source_snapshot: dict, heartbeat) -> None:
        root = Path(self.settings.render_scratch_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="beeftv-depth-verify-", dir=root) as temporary:
            path = Path(temporary) / "depth.mp4"
            with self._source(snapshot) as source, path.open("xb") as output:
                while block := source.read(1024**2):
                    heartbeat("检查深度视频…", 98)
                    output.write(block)
            measured = CanvasTimelineRenderer(self.settings).probe(path)
            if (
                not measured.facts.has_video
                or (measured.width, measured.height) != (1920, 1080)
                or measured.facts.duration_ms <= 0
                or abs(measured.facts.duration_ms - snapshot["duration_ms"]) > 1
                or (
                    source_snapshot.get("duration_ms")
                    and abs(measured.facts.duration_ms - source_snapshot["duration_ms"]) > 250
                )
            ):
                raise RuntimeError("已归档深度视频的尺寸、时长或字节校验失败")

    def _canvas_key(self, canvas_id: int | None) -> str | None:
        if canvas_id is None:
            return None
        with self.factory() as session:
            canvas = session.get(ProjectCanvas, canvas_id)
            if canvas is None:
                raise RenderCancelled()
            return canvas.source_key

    def execute(self, job_id: str, message_version: str) -> None:
        identifier, version, token = int(job_id), int(message_version), uuid4().hex
        job = self._claim(identifier, version, token)
        if job is None:
            return
        last_beat = 0.0

        def heartbeat(stage, progress, force=False):
            nonlocal last_beat
            if force or time.monotonic() - last_beat >= 0.5:
                self._heartbeat(identifier, token, stage, progress)
                last_beat = time.monotonic()

        try:
            heartbeat("检查来源…", 10, True)
            if job.task_type == "depth_capture":
                self._depth(job, token, heartbeat)
                return
            if job.task_type == "resource_playback":
                self._playback(job, token, heartbeat)
                return
            if job.task_type != "timeline_transcription":
                raise RuntimeError("此本地媒体任务执行类型暂不可用")
            model_id = job.request_json.get("modelConfigId")
            if model_id:
                if (job.result_json or {}).get("cloud_submission_started"):
                    raise RuntimeError("上一次云端转写提交结果待核实")

                def before_submit():
                    with self.factory.begin() as authority:
                        self._current(authority, identifier, token).result_json = {
                            "cloud_submission_started": True
                        }

                with self.factory.begin() as session:
                    self._current(session, identifier, token)
                    config = require_cloud_transcription(
                        CanvasLocalDAO(session).transcription_model(int(model_id), job.user_id),
                        job.request_json.get("modelConfigVersion"),
                    )
                    executor = CanvasCloudTranscriptionExecutor(
                        self.settings, config, heartbeat, before_submit=before_submit
                    )
            elif not self.settings.canvas_whisper_base_url.strip():
                raise RuntimeError(
                    "未配置本地转写服务：请设置 CANVAS_WHISPER_BASE_URL 指向 whisper.cpp 服务"
                )
            else:
                executor = CanvasTranscriptionExecutor(self.settings, heartbeat)
            with self._source(job.source_snapshot) as source:
                result = executor.run(
                    source, job.source_snapshot["mime_type"], job.request_json.get("language", "")
                )
            heartbeat("整理字幕…", 99, True)
            self._finish(identifier, token, result)
        except Exception as error:
            logger.warning(
                "Canvas local execution interrupted: job=%s error=%s",
                identifier,
                type(error).__name__,
            )
            self._fail(identifier, token, error)
