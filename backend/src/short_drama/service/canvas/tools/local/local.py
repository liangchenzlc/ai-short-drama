"""本人本地媒体任务的冻结准入、稳定重放与任务中心投影。"""

import json
from typing import Any

from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas.resources.resource import CanvasResourceDAO
from short_drama.dao.canvas.tools.local.local import CanvasLocalDAO
from short_drama.db.access.policies.canvas_local_access import canvas_local_write
from short_drama.domain import CanvasLocalJob, MediaFile, ProjectCanvas
from short_drama.schemas.canvas.tools.depth.depth import CanvasDepthCaptureCreate
from short_drama.schemas.canvas.tools.transcription.transcription import CanvasTranscriptionCreate
from short_drama.service.base import BaseService, utcnow
from short_drama.service.canvas.document.audit import iso
from short_drama.service.canvas.document.canvas import CanvasService
from short_drama.service.canvas.document.document import content_hash
from short_drama.service.canvas.timeline.timeline_compile import go_trim
from short_drama.service.canvas.tools.transcription.transcription_execute import (
    is_transcribable_mime,
)
from short_drama.storage.models import ObjectLocation


class CanvasLocalService(BaseService):
    model = CanvasLocalJob
    global_ordered = True

    def __init__(self, session: Session, settings: Settings) -> None:
        super().__init__(session)
        self.settings = settings
        self.jobs = CanvasLocalDAO(session)
        self.canvases = CanvasService(session)
        self.resources = CanvasResourceDAO(session)

    def _canvas(self, key: str) -> ProjectCanvas:
        value = self.resources.canvas(key)
        if value is None:
            raise NotFound("活动画布不存在或无权访问")
        return self.canvases.require_canvas(value.project_id, value.id, lock=True)

    def _scope(self, key: str | None, source: MediaFile) -> ProjectCanvas | None:
        if key is not None:
            return self._canvas(key)
        if source.project_id is None:
            return None
        self.canvases.require_project(source.project_id, lock=True)
        settings = self.canvases.canvas_dao.settings(source.project_id)
        if settings is None:
            raise NotFound("来源项目没有可用主画布")
        return self.canvases.require_canvas(
            source.project_id, settings.primary_canvas_id, lock=True
        )

    def create_transcription(
        self, payload: CanvasTranscriptionCreate, key: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        payload = CanvasTranscriptionCreate.model_validate(payload.model_dump(by_alias=True))
        request = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
        request["language"] = go_trim(payload.language)
        return self._create(payload, "timeline_transcription", request, key)

    def transcription_models(self) -> dict[str, Any]:
        from short_drama.service.canvas.tools.transcription.cloud_transcription import (
            supports_cloud_transcription,
        )

        with self._transaction(read_only=True):
            configs = self.jobs.transcription_models(self.canvases.actor_id)
            return {
                "models": [
                    {"id": str(config.id), "displayName": config.name}
                    for config in configs
                    if supports_cloud_transcription(config)
                ]
            }

    def create_depth_capture(
        self, payload: CanvasDepthCaptureCreate, key: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        payload = CanvasDepthCaptureCreate.model_validate(payload.model_dump(by_alias=True))
        request = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
        request["profile"] = "vda-small-mps-standard-v1"
        return self._create(payload, "depth_capture", request, key)

    def _create(
        self,
        payload: CanvasTranscriptionCreate | CanvasDepthCaptureCreate,
        task_type: str,
        request: dict[str, Any],
        key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        if key is not None and key != payload.client_operation_id:
            raise WorkflowError("canvas_local_operation_conflict", "操作标识与请求头不一致", 409)
        actor_id = self.canvases.actor_id
        request_hash = content_hash({"type": task_type, "input": request})
        with self._transaction(), canvas_local_write(self.session):
            previous = self.jobs.by_operation(actor_id, payload.client_operation_id, lock=False)
            if previous is not None:
                # 先按持久目标锁项目/画布；来源已回收也不能绕过原键重放或创建新任务。
                canvas = self._current_canvas(previous)
                previous = self.jobs.by_operation(actor_id, payload.client_operation_id)
                if previous.request_hash != request_hash:
                    raise WorkflowError(
                        "canvas_local_operation_conflict", "此标识已用于其他本地执行请求", 409
                    )
                return self._project(previous, canvas), False
            if task_type == "timeline_transcription" and payload.model_config_id is not None:
                from short_drama.service.canvas.tools.transcription.cloud_transcription import (
                    require_cloud_transcription,
                )

                config = require_cloud_transcription(
                    self.jobs.transcription_model(payload.model_config_id, actor_id)
                )
                request["modelConfigVersion"] = str(config.row_version)
                request["modelKey"] = config.model_key
            source = self.jobs.source(payload.resource_id)
            if source is None:
                raise NotFound("无法读取待处理媒体，可能已被删除")
            canvas = self._scope(payload.project_id, source)
            source = self.jobs.source(payload.resource_id, lock=True)
            if source is None or not (
                (source.scope_user_id == actor_id and source.project_id is None)
                or (canvas is not None and source.project_id == canvas.project_id)
            ):
                raise NotFound("待处理媒体不属于当前画布范围")
            if task_type in {"depth_capture", "resource_playback"}:
                if not source.format_code.startswith("video/"):
                    raise WorkflowError(
                        "canvas_depth_media_type", "深度动作捕捉仅支持视频资源", 400
                    )
                if task_type == "depth_capture" and (source.duration_ms or 0) > 15100:
                    raise WorkflowError(
                        "canvas_depth_video_too_long", "视频超过 15 秒，请先使用视频剪辑缩短", 400
                    )
            elif not is_transcribable_mime(source.format_code):
                raise WorkflowError("canvas_transcription_media_type", "仅支持音视频文件转写", 400)
            ObjectLocation.parse(
                source.storage_locator,
                {
                    self.settings.minio_image_bucket,
                    self.settings.minio_video_bucket,
                    self.settings.minio_audio_bucket,
                },
            )
            job = CanvasLocalJob(
                **self.canvases.audit(),
                project_id=canvas.project_id if canvas else None,
                canvas_id=canvas.id if canvas else None,
                canvas_version=canvas.row_version if canvas else 0,
                user_id=actor_id,
                task_type=task_type,
                client_operation_id=payload.client_operation_id,
                request_hash=request_hash,
                request_json=request,
                source_media_id=source.id,
                source_snapshot={
                    "media_id": str(source.id),
                    "locator": source.storage_locator,
                    "checksum": source.checksum_sha256,
                    "size": source.byte_size,
                    "mime_type": source.format_code,
                    "duration_ms": source.duration_ms,
                },
                status="queued",
                stage="检查深度处理组件" if task_type == "depth_capture" else "排队中",
                progress=5,
                attempts=0,
                message_version=1,
                next_run_at=utcnow(),
                cancel_requested=0,
            )
            self.session.add(job)
            self.session.flush()
            return self._project(job, canvas), True

    def _current_canvas(self, job: CanvasLocalJob) -> ProjectCanvas | None:
        return (
            self.canvases.require_canvas(job.project_id, job.canvas_id, lock=True)
            if job.canvas_id is not None
            else None
        )

    def maybe_detail(self, identifier: int) -> dict[str, Any] | None:
        with self._transaction(read_only=True):
            job = self.jobs.job(identifier, self.canvases.actor_id)
            if job is None:
                return None
            canvas = self._current_canvas(job)
            job = self.jobs.job(identifier, self.canvases.actor_id, lock=True)
            return self._project(job, canvas)

    def list(
        self, *, project_id=None, active_only=False, limit=30, client_operation_id=None
    ) -> list[dict[str, Any]]:
        with self._transaction(read_only=True):
            canvas_id = self._canvas(project_id).id if project_id is not None else None
            return [
                self._project(job, self._current_canvas(job))
                for job in self.jobs.list(
                    self.canvases.actor_id,
                    canvas_id=canvas_id,
                    active_only=active_only,
                    limit=limit,
                    client_operation_id=client_operation_id,
                )
            ]

    def maybe_action(self, identifier: int, action: str) -> dict[str, Any] | None:
        with self._transaction(), canvas_local_write(self.session):
            job = self.jobs.job(identifier, self.canvases.actor_id)
            if job is None:
                return None
            canvas = self._current_canvas(job)
            job = self.jobs.job(identifier, self.canvases.actor_id, lock=True)
            if action == "resume" and (
                job.disposal_receipt_id is not None
                or (job.error_json or {}).get("code") == "canvas_work_disposed"
            ):
                raise WorkflowError("canvas_work_disposed", "原画布作品已处置，任务不能恢复", 409)
            if action == "cancel" and job.status in {"queued", "running"}:
                job.cancel_requested = 1
                if job.status == "queued":
                    job.status, job.stage, job.finished_at = "cancelled", "已取消", utcnow()
            elif action == "resume":
                if (job.error_json or {}).get(
                    "code"
                ) == "canvas_transcription_submission_unknown" or (job.result_json or {}).get(
                    "cloud_submission_started"
                ):
                    raise WorkflowError(
                        "canvas_transcription_submission_unknown",
                        "云端提交结果待核实，不能恢复或自动重发",
                        409,
                    )
                if job.status not in {"failed", "cancelled"}:
                    raise WorkflowError(
                        "canvas_local_resume_unavailable", "此本地任务当前不能恢复", 409
                    )
                job.status, job.stage, job.progress = "queued", "排队中", 5
                job.message_version += 1
                job.cancel_requested, job.error_json, job.finished_at = 0, None, None
                job.next_run_at = utcnow()
            job.updated_at = utcnow()
            self.session.flush()
            return self._project(job, canvas)

    def _project(self, job: CanvasLocalJob, canvas: ProjectCanvas | None) -> dict[str, Any]:
        error = job.error_json or {}
        disposed = (
            job.disposal_receipt_id is not None or error.get("code") == "canvas_work_disposed"
        )
        value = {
            "id": str(job.id),
            "projectId": canvas.source_key if canvas else None,
            "type": job.task_type,
            "operation": job.task_type,
            "status": job.status,
            "progress": job.progress,
            "stage": job.stage,
            "prompt": {
                "timeline_transcription": "字幕转写",
                "resource_playback": "兼容视频预览",
            }.get(job.task_type, "深度动作捕捉"),
            "provider": "cloud" if job.request_json.get("modelConfigId") else "local",
            "model": job.request_json.get("modelKey", "whisper.cpp")
            if job.task_type == "timeline_transcription"
            else "h264-aac-v1"
            if job.task_type == "resource_playback"
            else "video-depth-anything-small",
            "clientOperationId": job.client_operation_id,
            "clientContext": {},
            "inputJson": json.dumps(
                job.request_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            "resultState": "READY"
            if job.status == "succeeded" and not disposed
            else "NOT_AVAILABLE",
            "outputs": [],
            "canRetry": job.status in {"failed", "cancelled"}
            and not disposed
            and not (job.result_json or {}).get("cloud_submission_started"),
            "canResume": job.status in {"failed", "cancelled"}
            and not disposed
            and not (job.result_json or {}).get("cloud_submission_started"),
            "canCancel": job.status in {"queued", "running"} and not job.cancel_requested,
            "attempts": job.attempts,
            "createdAt": iso(job.created_at),
            "updatedAt": iso(job.updated_at),
            "startedAt": iso(job.started_at) if job.started_at else None,
            "completedAt": iso(job.finished_at) if job.finished_at else None,
            "error": error.get("message"),
            "errorCode": error.get("code"),
        }
        if job.status == "succeeded" and job.result_json is not None and not disposed:
            value["resultJson"] = json.dumps(
                job.result_json, ensure_ascii=False, separators=(",", ":")
            )
        return value
