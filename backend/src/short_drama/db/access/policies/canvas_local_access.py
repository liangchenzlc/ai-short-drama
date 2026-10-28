"""HTTP 会话不能伪造本地执行快照、租约或完成回执。"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db.scope.canvas_scope import require_canvas
from short_drama.domain import CanvasLocalJob, MediaFile

_AUTHORITY = object()


@contextmanager
def canvas_local_write(session: Session) -> Iterator[None]:
    previous = session.info.get("canvas_local_authority")
    session.info["canvas_local_authority"] = _AUTHORITY
    try:
        yield
    finally:
        if previous is None:
            session.info.pop("canvas_local_authority", None)
        else:
            session.info["canvas_local_authority"] = previous


def guard_canvas_local(
    session: Session, job: CanvasLocalJob, actor: ActorContext, new: bool
) -> None:
    if job.user_id != actor.user_id:
        raise NotFound("本人本地执行任务不存在")
    if session.info.get("canvas_local_authority") is not _AUTHORITY or job in session.deleted:
        raise WorkflowError("canvas_local_authority_required", "本地任务只能由执行服务维护", 403)
    mutable = {
        "status",
        "stage",
        "progress",
        "cancel_requested",
        "error_json",
        "finished_at",
        "updated_at",
        "updated_by",
        "message_version",
        "next_run_at",
    }
    if not new and any(
        attr.history.has_changes() and attr.key not in mutable for attr in inspect(job).attrs
    ):
        raise WorkflowError(
            "canvas_local_snapshot_immutable", "本地任务身份、来源和回执不能改写", 403
        )
    if job.canvas_id is not None:
        require_canvas(session, job.project_id, job.canvas_id, lock=True)
    source = session.scalar(
        select(MediaFile).where(MediaFile.id == job.source_media_id).with_for_update(read=True)
    )
    if source is None or not (
        (source.scope_user_id == actor.user_id and source.project_id is None)
        or (source.project_id == job.project_id and job.project_id is not None)
    ):
        raise NotFound("待执行媒体不存在或不属于当前范围")
    if new and (
        job.status != "queued"
        or job.result_json is not None
        or job.output_media_id is not None
        or job.source_snapshot.get("media_id") != str(job.source_media_id)
    ):
        raise WorkflowError(
            "canvas_local_snapshot_immutable", "新本地任务必须从冻结来源队列开始", 403
        )
