"""HTTP会话不能伪造渲染快照、租约、媒体引用或完成回执。"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import CanvasRenderJob, CanvasRenderMediaReference

_AUTHORITY = object()


@contextmanager
def canvas_render_write(session: Session) -> Iterator[None]:
    previous = session.info.get("canvas_render_authority")
    session.info["canvas_render_authority"] = _AUTHORITY
    try:
        yield
    finally:
        if previous is None:
            session.info.pop("canvas_render_authority", None)
        else:
            session.info["canvas_render_authority"] = previous


def guard_canvas_render(
    session: Session,
    entity: CanvasRenderJob | CanvasRenderMediaReference,
    actor: ActorContext,
    new: bool,
) -> None:
    if entity.user_id != actor.user_id:
        raise NotFound("本人画布渲染任务不存在")
    if session.info.get("canvas_render_authority") is not _AUTHORITY or entity in session.deleted:
        raise WorkflowError("canvas_render_authority_required", "渲染任务只能由执行服务维护", 403)
    state = inspect(entity)
    mutable = (
        {
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
        if isinstance(entity, CanvasRenderJob)
        else set()
    )
    if not new and any(
        attr.history.has_changes() and attr.key not in mutable for attr in state.attrs
    ):
        raise WorkflowError("canvas_render_snapshot_immutable", "渲染身份、快照和回执不能改写", 403)
    job = (
        entity
        if isinstance(entity, CanvasRenderJob)
        else session.scalar(
            select(CanvasRenderJob).where(CanvasRenderJob.id == entity.job_id).with_for_update()
        )
    )
    if job is None or (entity.user_id, entity.project_id, entity.canvas_id) != (
        job.user_id,
        job.project_id,
        job.canvas_id,
    ):
        raise NotFound("渲染来源不属于此作者与画布")
    from short_drama.service.canvas_service import CanvasService

    CanvasService(session).require_canvas(job.project_id, job.canvas_id, lock=True)
    if (
        isinstance(entity, CanvasRenderJob)
        and new
        and (
            entity.status != "queued"
            or entity.output_media_id is not None
            or entity.result_json is not None
        )
    ):
        raise WorkflowError("canvas_render_snapshot_immutable", "新渲染任务必须从队列开始", 403)
    if isinstance(entity, CanvasRenderMediaReference):
        source = job.source_snapshot.get(entity.source_key)
        if not isinstance(source, dict) or source.get("media_id") != str(entity.media_id):
            raise WorkflowError("canvas_render_snapshot_immutable", "渲染引用必须对应冻结来源", 403)
