"""本人归档回执授权的窄恢复范围，不解除账号和成员权限检查。"""

from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CanvasRecycleScope:
    project_id: int
    canvas_id: int
    include_document: bool = False
    resource_id: int | None = None
    disposal_receipt: bool = False


def recycle_scope(session: Session) -> CanvasRecycleScope | None:
    scope = session.info.get("canvas_recycle_scope")
    return scope if isinstance(scope, CanvasRecycleScope) else None


@contextmanager
def restoring_archived_canvas(
    session: Session,
    project_id: int,
    canvas_id: int,
    *,
    include_document: bool = False,
    resource_id: int | None = None,
    disposal_receipt: bool = False,
):
    previous = session.info.get("canvas_recycle_scope")
    session.info["canvas_recycle_scope"] = CanvasRecycleScope(
        project_id, canvas_id, include_document, resource_id, disposal_receipt
    )
    try:
        yield
    finally:
        if previous is None:
            session.info.pop("canvas_recycle_scope", None)
        else:
            session.info["canvas_recycle_scope"] = previous
