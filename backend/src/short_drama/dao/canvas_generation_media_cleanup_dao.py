"""从本人已处置的执行记录定位生成媒体，不凭对象路径推测所有权。"""

from sqlalchemy import String, cast, exists, func, select
from sqlalchemy.orm import Session

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    MediaAsset,
    ProjectCanvas,
)
from short_drama.domain.canvas import CanvasWriteReceipt

from .canvas_recycle_cleanup_dao import PROGRESS_KEY, CanvasRecycleCleanupDAO
from .canvas_recycle_execution_dao import CanvasRecycleExecutionDAO


class CanvasGenerationMediaCleanupDAO:
    def __init__(self, session: Session):
        if session.info.get("actor"):
            raise ValueError("Generation media cleanup requires an internal maintenance session")
        self.session = session

    @staticmethod
    def origin(canvas_id: object) -> tuple:
        source = AIGenerationRecord.request_data["source"]
        return (
            source["canvas_id"].as_string() == cast(canvas_id, String),
            source["scene"].as_string().in_(("canvas_node", "canvas_tool")),
            AsyncTask.error["code"].as_string() == "canvas_work_disposed",
        )

    def candidates(self, after_id: int) -> list[tuple[int, int, int]]:
        receipt = CanvasWriteReceipt
        progress = ProjectCanvas.properties_json[PROGRESS_KEY]
        outputs = (
            select(MediaAsset.id)
            .join(AIGenerationRecord, AIGenerationRecord.id == MediaAsset.record_id)
            .join(AsyncTask, AsyncTask.id == AIGenerationRecord.task_id)
            .where(
                AsyncTask.initiated_by == receipt.actor_user_id,
                AsyncTask.project_id == receipt.project_id,
                *self.origin(receipt.canvas_id),
            )
        )
        query = (
            select(receipt.id, receipt.project_id, receipt.canvas_id)
            .join(ProjectCanvas, ProjectCanvas.id == receipt.canvas_id)
            .where(
                receipt.id > after_id,
                receipt.operation_kind == "canvas.recycle.purge",
                ProjectCanvas.project_id == receipt.project_id,
                ProjectCanvas.archived_at.is_not(None),
                ProjectCanvas.row_version == receipt.expected_row_version,
                func.json_length(ProjectCanvas.properties_json) == 1,
                progress["version"].as_integer() == 2,
                progress["receipt_id"].as_string() == cast(receipt.id, String),
                exists(outputs),
            )
            .order_by(receipt.id)
            .limit(100)
        )
        return [tuple(row) for row in self.session.execute(query)]

    def records(self, canvas: ProjectCanvas, actor_id: int) -> list[AIGenerationRecord]:
        tasks = list(
            self.session.scalars(
                select(AsyncTask)
                .where(
                    AsyncTask.initiated_by == actor_id,
                    AsyncTask.project_id == canvas.project_id,
                    AsyncTask.scope_user_id.is_(None),
                    AsyncTask.id.in_(
                        select(AIGenerationRecord.task_id).where(*self.origin(canvas.id))
                    ),
                )
                .order_by(AsyncTask.id)
                .with_for_update()
            )
        )
        maintenance = CanvasRecycleExecutionDAO(self.session)
        eligible = []
        for task in tasks:
            records = list(
                self.session.scalars(
                    select(AIGenerationRecord)
                    .where(AIGenerationRecord.task_id == task.id)
                    .order_by(AIGenerationRecord.call_no)
                    .with_for_update()
                )
            )
            if (
                task.status not in {"succeeded", "failed", "cancelled"}
                or task.lock_token is not None
                or task.next_action is not None
                or any(
                    record.status in {"sent", "unknown"}
                    or record.config_snapshot.get("agent_managed")
                    or record.request_data.get("batch_id")
                    or (record.request_data.get("source") or {}).get("canvas_id") != str(canvas.id)
                    or (record.request_data.get("source") or {}).get("project_id")
                    != str(canvas.project_id)
                    or (record.request_data.get("source") or {}).get("scene")
                    not in {"canvas_node", "canvas_tool"}
                    for record in records
                )
                or maintenance._protected(
                    task, records, ignored={"canvas_generation_media_deletions"}
                )
            ):
                continue
            eligible.extend(records)
        return eligible

    def results(
        self, canvas: ProjectCanvas, actor_id: int, task_id: int, media_id: int
    ) -> list[CanvasResult]:
        return list(
            self.session.scalars(
                select(CanvasResult)
                .join(CanvasTaskBinding, CanvasTaskBinding.id == CanvasResult.task_binding_id)
                .where(
                    CanvasResult.canvas_id == canvas.id,
                    CanvasResult.project_id == canvas.project_id,
                    CanvasResult.created_by == actor_id,
                    CanvasResult.media_id == media_id,
                    CanvasTaskBinding.initiated_by == actor_id,
                    CanvasTaskBinding.async_task_id == task_id,
                )
                .order_by(CanvasResult.id)
                .with_for_update()
            )
        )

    def lock_target(
        self, receipt_id: int, project_id: int, canvas_id: int
    ) -> tuple[ProjectCanvas, CanvasWriteReceipt] | None:
        dao = CanvasRecycleCleanupDAO(self.session)
        target = dao.lock_target(receipt_id, project_id, canvas_id, include_cleaned=True)
        if target is None or target[0].properties_json != dao.marker(receipt_id):
            return None
        return target
