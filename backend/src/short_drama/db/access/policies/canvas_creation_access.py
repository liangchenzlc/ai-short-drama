"""Creation drafts belong only to their author, including before a project exists."""

from sqlalchemy import inspect, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasCreationAttempt,
    CanvasCreationResource,
    MediaFile,
)


def guard_canvas_creation(
    session, entity: CanvasCreationAttempt | CanvasCreationResource, actor, new: bool
) -> None:
    if entity.user_id != actor.user_id:
        raise NotFound("Canvas creation does not exist")
    state = inspect(entity)
    common = {"id", "user_id", "created_at"}
    if isinstance(entity, CanvasCreationAttempt):
        immutable = common | {
            "idempotency_key",
            "operation_kind",
            "request_hash",
            "request_json",
            "source_key",
            "target_project_id",
        }
        previous = state.attrs.status.history.deleted
        if not new and (previous[0] if previous else entity.status) == "ready":
            raise WorkflowError("canvas_creation_immutable", "Creation receipt is fixed", 403)
    else:
        immutable = common | {
            "attempt_id",
            "source_resource_id",
            "snapshot_json",
            "target_resource_id",
            "storage_locator",
        }
        parent = next(
            (
                item
                for item in session.new
                if isinstance(item, CanvasCreationAttempt) and item.id == entity.attempt_id
            ),
            None,
        )
        if parent is None:
            parent = session.scalar(
                select(CanvasCreationAttempt)
                .where(CanvasCreationAttempt.id == entity.attempt_id)
                .with_for_update()
            )
        if parent is None or parent.user_id != actor.user_id:
            raise NotFound("Canvas creation does not exist")
        previous = state.attrs.status.history.deleted
        if not new and (previous[0] if previous else entity.status) == "attached":
            raise WorkflowError("canvas_creation_immutable", "Attached creation file is fixed", 403)
        for field, model in (
            ("source_media_id", MediaFile),
            ("source_binary_id", CanvasBinaryResource),
        ):
            identifier = getattr(entity, field)
            if identifier is not None and (
                identifier != entity.source_resource_id
                or session.scalar(select(model.id).where(model.id == identifier)) is None
            ):
                raise NotFound("Canvas creation source does not exist")
    if not new and any(state.attrs[field].history.has_changes() for field in immutable):
        raise WorkflowError("canvas_creation_immutable", "Creation request is fixed", 403)
    if (
        not new
        and session.scalar(
            select(type(entity).id).where(type(entity).id == entity.id).with_for_update()
        )
        is None
    ):
        raise NotFound("Canvas creation does not exist")
