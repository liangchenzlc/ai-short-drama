"""画布任务的作者范围、关联及确定性列表查询。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskBinding, ProjectCanvas


class CanvasGenerationDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def canvas(self, source_key: str) -> ProjectCanvas | None:
        return self.session.scalar(
            select(ProjectCanvas).where(
                ProjectCanvas.source_key == source_key, ProjectCanvas.archived_at.is_(None)
            )
        )

    def binding(self, task_id: int, actor_id: int) -> CanvasTaskBinding | None:
        return self.session.scalar(
            select(CanvasTaskBinding).where(
                CanvasTaskBinding.async_task_id == task_id,
                CanvasTaskBinding.initiated_by == actor_id,
            )
        )

    def task_ids(
        self,
        actor_id: int,
        canvas_id: int | None,
        active_only: bool,
        limit: int,
        client_operation_id: str | None = None,
        source_node_id: str | None = None,
    ) -> list[int]:
        statement = (
            select(AsyncTask.id)
            .join(CanvasTaskBinding, CanvasTaskBinding.async_task_id == AsyncTask.id)
            .join(ProjectCanvas, ProjectCanvas.id == CanvasTaskBinding.canvas_id)
            .where(
                CanvasTaskBinding.initiated_by == actor_id,
                AsyncTask.initiated_by == actor_id,
                ProjectCanvas.archived_at.is_(None),
            )
        )
        if canvas_id is not None:
            statement = statement.where(CanvasTaskBinding.canvas_id == canvas_id)
        if active_only:
            statement = statement.where(AsyncTask.status.in_(["queued", "running"]))
        if client_operation_id is not None:
            statement = statement.where(
                CanvasTaskBinding.client_operation_id == client_operation_id
            )
        if source_node_id is not None:
            statement = statement.where(CanvasTaskBinding.source_node_key == source_node_id)
        return list(
            self.session.scalars(
                statement.order_by(AsyncTask.updated_at.desc(), AsyncTask.id.desc()).limit(limit)
            )
        )

    def records(self, task_id: int) -> list[AIGenerationRecord]:
        return list(
            self.session.scalars(
                select(AIGenerationRecord)
                .where(AIGenerationRecord.task_id == task_id)
                .order_by(AIGenerationRecord.call_no)
                .with_for_update()
            )
        )
