"""画布渲染的作者查询与冻结媒体引用；不管理事务。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import CanvasRenderJob, MediaFile, ProjectCanvas


class CanvasRenderDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def job(self, identifier: int, user_id: int, *, lock=False) -> CanvasRenderJob | None:
        statement = select(CanvasRenderJob).where(
            CanvasRenderJob.id == identifier, CanvasRenderJob.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def by_operation(self, user_id: int, key: str) -> CanvasRenderJob | None:
        return self.session.scalar(
            select(CanvasRenderJob)
            .where(CanvasRenderJob.user_id == user_id, CanvasRenderJob.client_operation_id == key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def source(self, identifier: int) -> MediaFile | None:
        return self.session.scalar(
            select(MediaFile).where(MediaFile.id == identifier).with_for_update(read=True)
        )

    def list(
        self,
        user_id: int,
        *,
        canvas_id: int | None,
        active_only: bool,
        limit: int,
        client_operation_id: str | None,
    ) -> list[CanvasRenderJob]:
        query = (
            select(CanvasRenderJob)
            .join(ProjectCanvas, ProjectCanvas.id == CanvasRenderJob.canvas_id)
            .where(CanvasRenderJob.user_id == user_id, ProjectCanvas.archived_at.is_(None))
        )
        if canvas_id is not None:
            query = query.where(CanvasRenderJob.canvas_id == canvas_id)
        if active_only:
            query = query.where(CanvasRenderJob.status.in_(["queued", "running"]))
        if client_operation_id is not None:
            query = query.where(CanvasRenderJob.client_operation_id == client_operation_id)
        return list(
            self.session.scalars(
                query.order_by(CanvasRenderJob.updated_at.desc(), CanvasRenderJob.id.desc()).limit(
                    limit
                )
            )
        )
