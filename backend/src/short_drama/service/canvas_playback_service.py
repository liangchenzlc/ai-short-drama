"""按原资源版本复用本人兼容播放任务，原媒体身份和下载保持稳定。"""

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_local_dao import CanvasLocalDAO
from short_drama.domain import MediaFile
from short_drama.schemas.canvas_transcription import CanvasTranscriptionCreate

from .canvas_document import content_hash
from .canvas_local_service import CanvasLocalService


def playback_operation(source: MediaFile) -> str:
    digest = content_hash(
        {
            "locator": source.storage_locator,
            "checksum": source.checksum_sha256,
            "size": source.byte_size,
            "profile": "h264-aac-v1",
        }
    )
    return f"playback:{source.id}:{digest[:32]}"


class CanvasPlaybackService(CanvasLocalService):
    def prepare(self, resource_id: int, canvas_key: str | None = None) -> None:
        with self._transaction(read_only=True):
            source = self.jobs.source(resource_id)
            if source is None:
                raise NotFound("视频资源不存在或无权访问")
            if not source.format_code.startswith("video/"):
                raise WorkflowError("canvas_playback_video_required", "仅视频支持兼容播放", 422)
            if canvas_key is not None:
                self._canvas(canvas_key)
            operation = playback_operation(source)
        payload = CanvasTranscriptionCreate(
            resourceId=str(resource_id), clientOperationId=operation
        )
        result, _ = self._create(
            payload,
            "resource_playback",
            {
                "resourceId": str(resource_id),
                "clientOperationId": operation,
                "profile": "h264-aac-v1",
            },
            None,
        )
        if result["status"] in {"failed", "cancelled"}:
            self.maybe_action(int(result["id"]), "resume")


def playback_projection(session, source, actor_id: int) -> dict:
    if not isinstance(source, MediaFile) or not source.format_code.startswith("video/"):
        return {}
    job = CanvasLocalDAO(session).by_operation(actor_id, playback_operation(source), lock=False)
    if job is None or job.disposal_receipt_id is not None:
        return {}
    status = {"succeeded": "ready", "failed": "failed", "cancelled": "failed"}.get(
        job.status, "processing"
    )
    return {
        "playback_status": status,
        "playback_error": (job.error_json or {}).get("message", ""),
        "playback_task_id": str(job.id),
    }


def playback_output(session, source, actor_id: int) -> int:
    job = CanvasLocalDAO(session).by_operation(actor_id, playback_operation(source), lock=False)
    if job is None or job.status != "succeeded" or job.output_media_id is None:
        raise WorkflowError("canvas_playback_not_ready", "兼容预览尚未就绪，原文件仍可下载", 409)
    return job.output_media_id
