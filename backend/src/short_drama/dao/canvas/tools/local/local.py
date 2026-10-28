"""本人本地执行任务与冻结来源查询；事务由 Service 管理。"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from short_drama.domain import AIModelConfig, CanvasLocalJob, MediaFile, ProjectCanvas


class CanvasLocalDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def job(self, identifier: int, user_id: int, *, lock=False) -> CanvasLocalJob | None:
        query = select(CanvasLocalJob).where(
            CanvasLocalJob.id == identifier, CanvasLocalJob.user_id == user_id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def by_operation(self, user_id: int, key: str, *, lock: bool = True) -> CanvasLocalJob | None:
        query = select(CanvasLocalJob).where(
            CanvasLocalJob.user_id == user_id, CanvasLocalJob.client_operation_id == key
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def source(self, identifier: int, *, lock=False) -> MediaFile | None:
        query = select(MediaFile).where(MediaFile.id == identifier)
        if lock:
            query = query.with_for_update(read=True).execution_options(populate_existing=True)
        return self.session.scalar(query)

    def transcription_models(self, user_id: int) -> list[AIModelConfig]:
        return list(
            self.session.scalars(
                select(AIModelConfig)
                .where(
                    AIModelConfig.owner_user_id == user_id,
                    AIModelConfig.enabled == 1,
                    AIModelConfig.is_deleted == 0,
                    AIModelConfig.service_type == "audio",
                )
                .order_by(AIModelConfig.id)
            )
        )

    def transcription_model(self, identifier: int, user_id: int) -> AIModelConfig | None:
        return self.session.scalar(
            select(AIModelConfig)
            .where(
                AIModelConfig.id == identifier,
                AIModelConfig.owner_user_id == user_id,
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
                AIModelConfig.service_type == "audio",
            )
            .with_for_update(read=True)
        )

    def list(
        self, user_id: int, *, canvas_id=None, active_only=False, limit=30, client_operation_id=None
    ) -> list[CanvasLocalJob]:
        query = (
            select(CanvasLocalJob)
            .outerjoin(ProjectCanvas, ProjectCanvas.id == CanvasLocalJob.canvas_id)
            .where(
                CanvasLocalJob.user_id == user_id,
                or_(CanvasLocalJob.canvas_id.is_(None), ProjectCanvas.archived_at.is_(None)),
            )
        )
        if canvas_id is not None:
            query = query.where(CanvasLocalJob.canvas_id == canvas_id)
        if active_only:
            query = query.where(CanvasLocalJob.status.in_(["queued", "running"]))
        if client_operation_id is not None:
            query = query.where(CanvasLocalJob.client_operation_id == client_operation_id)
        return list(
            self.session.scalars(
                query.order_by(CanvasLocalJob.updated_at.desc(), CanvasLocalJob.id.desc()).limit(
                    limit
                )
            )
        )
