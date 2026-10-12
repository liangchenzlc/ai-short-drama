"""私人本地渲染准入、稳定重放与任务中心投影。"""

import json
import re

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.canvas_render_dao import CanvasRenderDAO
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.db.canvas_render_access import canvas_render_write
from short_drama.domain import CanvasRenderJob, CanvasRenderMediaReference
from short_drama.schemas.canvas_timeline import (
    CanvasTimelinePlanRequest,
    CanvasTimelineRenderRequest,
)
from short_drama.storage.models import ObjectLocation

from .base import BaseService, utcnow
from .canvas_document import content_hash
from .canvas_service import CanvasService, iso
from .canvas_timeline_compile import compile_timeline_plan, fail
from .canvas_timeline_native import source_ids


class CanvasRenderService(BaseService):
    model = CanvasRenderJob
    global_ordered = True

    def __init__(self, session, settings):
        super().__init__(session)
        self.settings = settings
        self.jobs = CanvasRenderDAO(session)
        self.canvases = CanvasService(session)
        self.resources = CanvasResourceDAO(session)

    def _canvas(self, key):
        canvas = self.resources.canvas(key)
        if canvas is None:
            from short_drama.core.exceptions import NotFound

            raise NotFound("活动画布不存在或无权访问")
        return self.canvases.require_canvas(canvas.project_id, canvas.id, lock=True)

    def create(self, payload: CanvasTimelineRenderRequest, key=None) -> tuple[dict, bool]:
        payload = CanvasTimelineRenderRequest.model_validate(payload.model_dump(by_alias=True))
        if key is not None and key != payload.client_operation_id:
            raise WorkflowError("canvas_render_operation_conflict", "操作标识与请求头不一致", 409)
        request = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
        request_hash = content_hash(request)
        actor_id = self.canvases.actor_id
        with self._transaction(), canvas_render_write(self.session):
            canvas = self._canvas(payload.project_id)
            previous = self.jobs.by_operation(actor_id, payload.client_operation_id)
            if previous is not None:
                if previous.request_hash != request_hash or previous.canvas_id != canvas.id:
                    raise WorkflowError(
                        "canvas_render_operation_conflict", "此标识已用于其他渲染请求", 409
                    )
                return self._project(previous, canvas), False
            plan = compile_timeline_plan(
                CanvasTimelinePlanRequest(timeline=payload.timeline, options=payload.options)
            )
            if plan.duration_ms > self.settings.render_max_duration_ms:
                raise WorkflowError(
                    "canvas_render_duration_limit", "时间线超过本机成片时长上限", 400
                )
            snapshot = {}
            for identifier in source_ids(plan):
                if not re.fullmatch(r"[1-9][0-9]{0,19}", identifier) or int(identifier) >= 2**64:
                    fail("missing_source", identifier)
                media = self.jobs.source(int(identifier))
                if media is None or (
                    media.project_id != canvas.project_id and media.scope_user_id != actor_id
                ):
                    fail("missing_source", identifier)
                ObjectLocation.parse(
                    media.storage_locator,
                    {
                        self.settings.minio_image_bucket,
                        self.settings.minio_video_bucket,
                        self.settings.minio_audio_bucket,
                    },
                )
                snapshot[identifier] = {
                    "media_id": str(media.id),
                    "locator": media.storage_locator,
                    "checksum": media.checksum_sha256,
                    "mime_type": media.format_code,
                    "size": media.byte_size,
                }
            job = CanvasRenderJob(
                **self.canvases.child(canvas),
                user_id=actor_id,
                client_operation_id=payload.client_operation_id,
                request_hash=request_hash,
                request_json=request,
                plan_json=plan.model_dump(mode="json", by_alias=True),
                source_snapshot=snapshot,
                canvas_version=canvas.row_version,
                status="queued",
                stage="排队中",
                progress=5,
                attempts=0,
                message_version=1,
                next_run_at=utcnow(),
                cancel_requested=0,
            )
            self.session.add(job)
            self.session.flush()
            for ordinal, (identifier, source) in enumerate(snapshot.items()):
                self.session.add(
                    CanvasRenderMediaReference(
                        **self.canvases.child(canvas),
                        user_id=actor_id,
                        job_id=job.id,
                        source_key=identifier,
                        ordinal=ordinal,
                        media_id=int(source["media_id"]),
                    )
                )
            self.session.flush()
            return self._project(job, canvas), True

    def maybe_detail(self, identifier: int) -> dict | None:
        with self._transaction(read_only=True):
            job = self.jobs.job(identifier, self.canvases.actor_id)
            if job is None:
                return None
            canvas = self.canvases.require_canvas(job.project_id, job.canvas_id, lock=True)
            job = self.jobs.job(identifier, self.canvases.actor_id, lock=True)
            return self._project(job, canvas)

    def list(self, *, project_id=None, active_only=False, limit=30, client_operation_id=None):
        with self._transaction(read_only=True):
            canvas_id = self._canvas(project_id).id if project_id is not None else None
            jobs = self.jobs.list(
                self.canvases.actor_id,
                canvas_id=canvas_id,
                active_only=active_only,
                limit=limit,
                client_operation_id=client_operation_id,
            )
            return [
                self._project(job, self.canvases.require_canvas(job.project_id, job.canvas_id))
                for job in jobs
            ]

    def maybe_action(self, identifier: int, action: str) -> dict | None:
        with self._transaction(), canvas_render_write(self.session):
            job = self.jobs.job(identifier, self.canvases.actor_id)
            if job is None:
                return None
            canvas = self.canvases.require_canvas(job.project_id, job.canvas_id, lock=True)
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
                if job.status not in {"failed", "cancelled"}:
                    raise WorkflowError(
                        "canvas_render_resume_unavailable", "此渲染任务当前不能恢复", 409
                    )
                job.status, job.stage, job.progress = "queued", "排队中", 5
                job.message_version += 1
                job.cancel_requested, job.error_json, job.finished_at = 0, None, None
                job.next_run_at = utcnow()
            job.updated_at = utcnow()
            self.session.flush()
            return self._project(job, canvas)

    def _project(self, job, canvas) -> dict:
        error = job.error_json or {}
        disposed = (
            job.disposal_receipt_id is not None or error.get("code") == "canvas_work_disposed"
        )
        value = {
            "id": str(job.id),
            "projectId": canvas.source_key,
            "type": "timeline_render",
            "operation": "timeline_render",
            "status": job.status,
            "progress": job.progress,
            "stage": job.stage,
            "prompt": "时间线渲染",
            "provider": "local",
            "model": "ffmpeg",
            "clientOperationId": job.client_operation_id,
            "clientContext": {},
            "inputJson": json.dumps(
                job.request_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            "resultState": "READY"
            if job.status == "succeeded" and not disposed
            else "NOT_AVAILABLE",
            "outputs": [],
            "canRetry": job.status in {"failed", "cancelled"} and not disposed,
            "canResume": job.status in {"failed", "cancelled"} and not disposed,
            "canCancel": job.status in {"queued", "running"} and not job.cancel_requested,
            "attempts": job.attempts,
            "createdAt": iso(job.created_at),
            "updatedAt": iso(job.updated_at),
            "error": error.get("message"),
            "errorCode": error.get("code"),
        }
        if job.result_json is not None and not disposed:
            value["resultJson"] = json.dumps(
                job.result_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        if job.started_at:
            value["startedAt"] = iso(job.started_at)
        if job.finished_at:
            value["completedAt"] = iso(job.finished_at)
        return value
