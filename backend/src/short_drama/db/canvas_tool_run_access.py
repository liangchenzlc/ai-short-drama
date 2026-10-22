"""租约与批准只能由检查过来源的工具Service写入，不能继承项目成员权限。"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db.canvas_scope import require_canvas
from short_drama.domain import CanvasTaskBinding, CanvasToolRun, CanvasToolSubmission
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate

_AUTHORITY = object()


def require_approved_tool_submission(
    session: Session, payload: CanvasRuntimeTaskCreate, item: CanvasToolSubmission
) -> None:
    from short_drama.core.time import utcnow
    from short_drama.utils.canvas_document import content_hash

    actor = session.info.get("actor")
    state = inspect(item)
    run = session.scalar(select(CanvasToolRun).where(CanvasToolRun.id == item.run_id))
    if (
        session.info.get("canvas_tool_authority") is not _AUTHORITY
        or state.session is not session
        or not state.persistent
        or actor is None
        or item.user_id != actor.user_id
        or item.approved_at is None
        or item.async_task_id is not None
        or run is None
        or run.user_id != actor.user_id
        or run.lease_expires_at is None
        or run.lease_expires_at <= utcnow()
        or item.request_hash
        != content_hash(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
    ):
        raise WorkflowError("canvas_tool_approval_required", "分析阶段缺少可信批准回执", 409)


@contextmanager
def tool_run_write(session: Session) -> Iterator[None]:
    previous = session.info.get("canvas_tool_authority")
    session.info["canvas_tool_authority"] = _AUTHORITY
    try:
        yield
    finally:
        if previous is None:
            session.info.pop("canvas_tool_authority", None)
        else:
            session.info["canvas_tool_authority"] = previous


def guard_canvas_tool_run(
    session: Session,
    entity: CanvasToolRun | CanvasToolSubmission,
    actor: ActorContext,
    new: bool,
) -> None:
    if entity.user_id != actor.user_id:
        raise NotFound("本人分析执行记录不存在")
    if session.info.get("canvas_tool_authority") is not _AUTHORITY or entity in session.deleted:
        raise WorkflowError("canvas_tool_authority_required", "分析授权只能由工具执行服务维护", 403)
    state = inspect(entity)
    mutable = {"updated_at", "updated_by"} | (
        {"execution_epoch", "execution_owner", "lease_expires_at"}
        if isinstance(entity, CanvasToolRun)
        else {"approved_at", "async_task_id"}
    )
    if not new and any(
        attr.history.has_changes() and attr.key not in mutable for attr in state.attrs
    ):
        raise WorkflowError(
            "canvas_tool_request_immutable", "分析身份、来源与阶段请求不能改写", 403
        )
    run = (
        entity
        if isinstance(entity, CanvasToolRun)
        else session.scalar(
            select(CanvasToolRun).where(CanvasToolRun.id == entity.run_id).with_for_update()
        )
    )
    if run is None or (entity.user_id, entity.project_id, entity.canvas_id) != (
        run.user_id,
        run.project_id,
        run.canvas_id,
    ):
        raise NotFound("分析阶段不属于此作者与画布")
    require_canvas(session, run.project_id, run.canvas_id, lock=True)
    if isinstance(entity, CanvasToolSubmission):
        if new and (entity.approved_at is not None or entity.async_task_id is not None):
            raise WorkflowError("canvas_tool_approval_required", "新阶段必须先准备再批准", 403)
        if not new:
            stored = session.execute(
                select(CanvasToolSubmission.approved_at, CanvasToolSubmission.async_task_id)
                .where(CanvasToolSubmission.id == entity.id)
                .with_for_update()
            ).one_or_none()
            if stored is None:
                raise NotFound("分析阶段不存在")
            if (stored.approved_at is not None and stored.approved_at != entity.approved_at) or (
                stored.async_task_id is not None and stored.async_task_id != entity.async_task_id
            ):
                raise WorkflowError("canvas_tool_receipt_immutable", "批准与任务回执不能替换", 403)
        if entity.async_task_id is not None:
            binding = session.scalar(
                select(CanvasTaskBinding).where(
                    CanvasTaskBinding.async_task_id == entity.async_task_id
                )
            )
            if binding is None or (
                binding.initiated_by,
                binding.project_id,
                binding.canvas_id,
                binding.node_key,
            ) != (run.user_id, run.project_id, run.canvas_id, run.state_json["nodeId"]):
                raise NotFound("阶段任务不属于此作者、来源与画布")
