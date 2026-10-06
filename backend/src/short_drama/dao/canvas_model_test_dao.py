"""本人独立模型测试的任务中心索引与真实媒体结果。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import AIGenerationRecord, AsyncTask, MediaAsset, MediaFile


class CanvasModelTestDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def task_ids(
        self,
        actor_id: int,
        *,
        active_only: bool,
        limit: int,
        client_operation_id: str | None = None,
    ) -> list[int]:
        statement = (
            select(AsyncTask.id)
            .join(AIGenerationRecord, AIGenerationRecord.task_id == AsyncTask.id)
            .where(
                AsyncTask.initiated_by == actor_id,
                AsyncTask.scope_user_id == actor_id,
                AsyncTask.project_id.is_(None),
                AIGenerationRecord.call_no == 1,
                AIGenerationRecord.request_data["source"]["scene"].as_string()
                == "canvas_model_test",
                AIGenerationRecord.config_snapshot["canvas_model_test"].as_boolean().is_(True),
            )
        )
        if active_only:
            statement = statement.where(AsyncTask.status.in_(["queued", "running"]))
        if client_operation_id is not None:
            statement = statement.where(
                AIGenerationRecord.request_data["canvas_request"]["input"]["metadata"][
                    "clientOperationId"
                ].as_string()
                == client_operation_id
            )
        return list(
            self.session.scalars(
                statement.order_by(AsyncTask.updated_at.desc(), AsyncTask.id.desc()).limit(limit)
            )
        )

    def media(self, record_ids: list[int]) -> list[tuple[MediaAsset, MediaFile]]:
        return list(
            self.session.execute(
                select(MediaAsset, MediaFile)
                .join(MediaFile, MediaFile.id == MediaAsset.media_id)
                .where(MediaAsset.record_id.in_(record_ids))
                .order_by(MediaAsset.output_index)
            ).tuples()
        )
