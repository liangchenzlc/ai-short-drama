"""风格快照只能由校验服务写入；禁止绕过完整快照与引用同步。"""

from contextlib import contextmanager

from sqlalchemy import inspect

from short_drama.core.exceptions import WorkflowError

_AUTHORITY = object()


@contextmanager
def canvas_style_write(session):
    previous = session.info.get("canvas_style_authority")
    session.info["canvas_style_authority"] = _AUTHORITY
    try:
        yield
    finally:
        if previous is None:
            session.info.pop("canvas_style_authority", None)
        else:
            session.info["canvas_style_authority"] = previous


def guard_canvas_style(session, entity, actor, new):
    if session.info.get("canvas_style_authority") is not _AUTHORITY:
        raise WorkflowError("canvas_style_authority_required", "画风必须通过快照保存服务维护", 403)
    if (
        not new
        and entity.__tablename__ == "canvas_style_media_references"
        and any(
            inspect(entity).attrs[field].history.has_changes() for field in ("style_id", "media_id")
        )
    ):
        raise WorkflowError("ownership_immutable", "画风资源引用不可改写", 403)
