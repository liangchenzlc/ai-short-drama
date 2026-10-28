from sqlalchemy import Row, select
from sqlalchemy.orm import Session

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    MediaAsset,
    MediaFile,
    ProjectCanvas,
)


class CanvasTaskDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def canvas(self, source_key: str) -> ProjectCanvas | None:
        return self.session.scalar(
            select(ProjectCanvas).where(
                ProjectCanvas.source_key == source_key, ProjectCanvas.archived_at.is_(None)
            )
        )

    def task(self, task_id: int) -> AsyncTask | None:
        return self.session.scalar(
            select(AsyncTask).where(AsyncTask.id == task_id).with_for_update()
        )

    def binding(self, task_id: int) -> CanvasTaskBinding | None:
        return self.session.scalar(
            select(CanvasTaskBinding)
            .where(CanvasTaskBinding.async_task_id == task_id)
            .with_for_update()
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

    def result(self, binding_id: int, index: int) -> CanvasResult | None:
        return self.session.scalar(
            select(CanvasResult)
            .where(CanvasResult.task_binding_id == binding_id, CanvasResult.result_index == index)
            .with_for_update()
        )

    def output(self, record_id: int, index: int) -> Row[tuple[MediaAsset, MediaFile]] | None:
        return self.session.execute(
            select(MediaAsset, MediaFile)
            .join(MediaFile, MediaFile.id == MediaAsset.media_id)
            .where(MediaAsset.record_id == record_id, MediaAsset.output_index == index + 1)
            .with_for_update()
        ).first()

    def outputs(self, record_id: int) -> list[Row[tuple[MediaAsset, MediaFile]]]:
        return list(
            self.session.execute(
                select(MediaAsset, MediaFile)
                .join(MediaFile, MediaFile.id == MediaAsset.media_id)
                .where(MediaAsset.record_id == record_id)
                .order_by(MediaAsset.output_index)
                .with_for_update()
            ).all()
        )

    def media(self, identifier: int) -> MediaFile | None:
        return self.session.scalar(
            select(MediaFile).where(MediaFile.id == identifier).with_for_update()
        )
