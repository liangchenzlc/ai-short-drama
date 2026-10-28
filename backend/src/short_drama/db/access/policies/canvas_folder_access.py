"""Private folder assignments never grant access to the referenced canvas."""

from sqlalchemy import inspect, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import CanvasBinaryResource, CanvasProjectFolder, MediaFile, ProjectCanvas


def guard_canvas_folder(session, entity, actor, new):
    if entity.user_id != actor.user_id:
        raise NotFound("Canvas folder does not exist")
    state = inspect(entity)
    if not new:
        if any(
            field in state.attrs and state.attrs[field].history.has_changes()
            for field in ("user_id", "source_key", "canvas_id")
        ):
            raise WorkflowError("ownership_immutable", "Canvas folder identity is fixed", 403)
        if (
            session.scalar(
                select(type(entity).id).where(type(entity).id == entity.id).with_for_update()
            )
            is None
        ):
            raise NotFound("Canvas folder does not exist")
    if isinstance(entity, CanvasProjectFolder):
        if entity in session.deleted:
            raise WorkflowError("canvas_folder_tombstone_required", "文件夹必须保留删除记录", 409)
        history = state.attrs.tombstoned_at.history
        if (
            not new
            and history.has_changes()
            and any(value is not None for value in history.deleted)
        ):
            raise WorkflowError("canvas_folder_deleted", "已删除文件夹不能恢复", 409)
        for field, model in (
            ("cover_media_id", MediaFile),
            ("cover_binary_id", CanvasBinaryResource),
        ):
            identifier = getattr(entity, field)
            if identifier is None:
                continue
            resource = session.scalar(select(model).where(model.id == identifier))
            if resource is None or resource.created_by != actor.user_id:
                raise NotFound("Canvas folder cover does not exist")
        return
    # Removing an owned classification remains possible after project revocation;
    # assigning/moving it always rechecks access to the actual work.
    if entity in session.deleted:
        return
    folder = session.scalar(
        select(CanvasProjectFolder).where(CanvasProjectFolder.source_key == entity.folder_key)
    )
    if folder is None or folder.tombstoned_at is not None:
        raise NotFound("Canvas folder does not exist")
    canvas = session.scalar(select(ProjectCanvas).where(ProjectCanvas.id == entity.canvas_id))
    if canvas is None or canvas.archived_at is not None:
        raise NotFound("Canvas does not exist")
