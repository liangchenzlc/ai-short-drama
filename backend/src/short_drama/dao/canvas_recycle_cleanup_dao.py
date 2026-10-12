"""内部回收清理查询；处置回执和归档代次是唯一授权来源。"""

from sqlalchemy import String, and_, cast, delete, func, select, update
from sqlalchemy.orm import Session

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    Base,
    CanvasLocalJob,
    CanvasRenderJob,
    CanvasResourceUpload,
    CanvasToolRun,
    Project,
    ProjectCanvas,
)
from short_drama.domain.canvas import CanvasWriteReceipt
from short_drama.tasks.state import TERMINAL

from .canvas_creation_dao import CanvasCreationDAO
from .canvas_recycle_execution_dao import CanvasRecycleExecutionDAO

PROGRESS_KEY = "$recycle_cleanup"

# 按实际外键顺序删除作品载荷；执行记录、回执和画布身份保留原生命周期。
PAYLOAD_TABLES = (
    "canvas_binary_references",
    "canvas_user_binary_references",
    "canvas_user_media_references",
    "canvas_media_references",
    "canvas_revision_media_references",
    "canvas_revision_user_states",
    "canvas_revision_drawing_references",
    "canvas_node_user_states",
    "canvas_user_states",
    "canvas_edges",
    "canvas_timelines",
    "canvas_director_scenes",
    "canvas_revisions",
    "canvas_drawing_media_references",
    "canvas_drawing_versions",
    "canvas_drawings",
    "canvas_nodes",
    "canvas_project_folder_items",
)


class CanvasRecycleCleanupDAO:
    def __init__(self, session: Session):
        if session.info.get("actor"):
            raise ValueError("Recycle cleanup requires an internal maintenance session")
        self.session = session

    @staticmethod
    def marker(receipt_id: int) -> dict:
        return {PROGRESS_KEY: {"version": 2, "receipt_id": str(receipt_id)}}

    def candidates(self, after_id: int) -> list[tuple[int, int, int]]:
        progress = ProjectCanvas.properties_json[PROGRESS_KEY]
        done = and_(
            func.json_length(ProjectCanvas.properties_json) == 1,
            progress["version"].as_integer() == 2,
            progress["receipt_id"].as_string() == cast(CanvasWriteReceipt.id, String),
        )
        pending_creation = CanvasCreationDAO.disposed_query(
            ProjectCanvas.project_id,
            ProjectCanvas.id,
            ProjectCanvas.source_key,
            CanvasWriteReceipt.actor_user_id,
        ).exists()
        pending_local = CanvasRecycleExecutionDAO.pending_local(
            ProjectCanvas.project_id, ProjectCanvas.id, CanvasWriteReceipt.actor_user_id
        )
        pending_legacy_media = CanvasCreationDAO.pending_legacy_media(
            ProjectCanvas.project_id,
            ProjectCanvas.id,
            ProjectCanvas.source_key,
            CanvasWriteReceipt.actor_user_id,
        )
        # 普通文档属性仍有源id等字段，不能用客户端带来的同名字段冒充完成标记。
        query = (
            select(
                CanvasWriteReceipt.id, CanvasWriteReceipt.project_id, CanvasWriteReceipt.canvas_id
            )
            .join(ProjectCanvas, ProjectCanvas.id == CanvasWriteReceipt.canvas_id)
            .join(Project, Project.id == CanvasWriteReceipt.project_id)
            .where(
                CanvasWriteReceipt.id > after_id,
                CanvasWriteReceipt.operation_kind == "canvas.recycle.purge",
                Project.workspace_mode == "infinite_canvas",
                ProjectCanvas.project_id == CanvasWriteReceipt.project_id,
                ProjectCanvas.archived_at.is_not(None),
                ProjectCanvas.row_version == CanvasWriteReceipt.expected_row_version,
                ~func.coalesce(done, False)
                | pending_creation
                | pending_local
                | pending_legacy_media,
            )
            .order_by(CanvasWriteReceipt.id)
            .limit(100)
        )
        return [tuple(row) for row in self.session.execute(query)]

    def lock_target(
        self, receipt_id: int, project_id: int, canvas_id: int, *, include_cleaned: bool = False
    ) -> tuple[ProjectCanvas, CanvasWriteReceipt] | None:
        # 与恢复、保存和Worker一致：先项目，再画布，再操作/任务记录。
        project = self.session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        canvas = self.session.scalar(
            select(ProjectCanvas).where(ProjectCanvas.id == canvas_id).with_for_update()
        )
        receipt = self.session.scalar(
            select(CanvasWriteReceipt).where(CanvasWriteReceipt.id == receipt_id).with_for_update()
        )
        if (
            project is None
            or project.workspace_mode != "infinite_canvas"
            or canvas is None
            or canvas.project_id != project_id
            or canvas.archived_at is None
            or receipt is None
            or receipt.operation_kind != "canvas.recycle.purge"
            or receipt.project_id != project_id
            or receipt.canvas_id != canvas_id
            or canvas.row_version != receipt.expected_row_version
        ):
            return None
        archived = self.session.scalar(
            select(CanvasWriteReceipt)
            .where(
                CanvasWriteReceipt.actor_user_id == receipt.actor_user_id,
                CanvasWriteReceipt.idempotency_key == receipt.result_json.get("archive_key"),
                CanvasWriteReceipt.operation_kind == "canvas.archive",
                CanvasWriteReceipt.canvas_id == canvas_id,
                CanvasWriteReceipt.project_id == project_id,
                CanvasWriteReceipt.committed_row_version == canvas.row_version,
            )
            .with_for_update()
        )
        if archived is None or receipt.result_json.get("source_key") != canvas.source_key:
            raise ValueError("Recycle disposal does not identify its exact archived work")
        if (
            not include_cleaned
            and canvas.properties_json == self.marker(receipt.id)
            and not self.session.scalar(
                select(
                    CanvasCreationDAO.disposed_query(
                        project_id, canvas_id, canvas.source_key, receipt.actor_user_id
                    ).exists()
                    | CanvasRecycleExecutionDAO.pending_local(
                        project_id, canvas_id, receipt.actor_user_id
                    )
                    | CanvasCreationDAO.pending_legacy_media(
                        project_id, canvas_id, canvas.source_key, receipt.actor_user_id
                    )
                )
            )
        ):
            return None
        return canvas, receipt

    def executing(self, canvas_id: int) -> bool:
        identifiers = CanvasRecycleExecutionDAO(self.session).task_ids(canvas_id)
        tasks = list(
            self.session.scalars(
                select(AsyncTask).where(AsyncTask.id.in_(identifiers)).with_for_update()
            )
        )
        if any(task.status not in TERMINAL or task.lock_token is not None for task in tasks):
            return True
        checks = (
            select(AIGenerationRecord.id).where(
                AIGenerationRecord.task_id.in_(identifiers),
                AIGenerationRecord.status.in_(("sent", "unknown")),
            ),
            select(CanvasToolRun.id).where(
                CanvasToolRun.canvas_id == canvas_id, CanvasToolRun.execution_owner != ""
            ),
            *(
                select(model.id).where(model.canvas_id == canvas_id, model.status.not_in(TERMINAL))
                for model in (CanvasLocalJob, CanvasRenderJob)
            ),
        )
        return any(
            self.session.scalar(query.limit(1).with_for_update()) is not None for query in checks
        )

    def uploads(self, canvas_id: int, actor_id: int) -> list[CanvasResourceUpload]:
        return list(
            self.session.scalars(
                select(CanvasResourceUpload)
                .where(
                    CanvasResourceUpload.canvas_id == canvas_id,
                    CanvasResourceUpload.user_id == actor_id,
                    CanvasResourceUpload.status == "ready",
                )
                .order_by(CanvasResourceUpload.id)
                .with_for_update()
            )
        )

    def remove_payload(self, canvas_id: int) -> None:
        nodes = Base.metadata.tables["canvas_nodes"]
        self.session.execute(
            update(nodes).where(nodes.c.canvas_id == canvas_id).values(parent_node_key=None)
        )
        for name in PAYLOAD_TABLES:
            table = Base.metadata.tables[name]
            self.session.execute(delete(table).where(table.c.canvas_id == canvas_id))
        # 只擦除内部恢复预览；公开回执内容、请求hash和版本保持不变。
        receipts = list(
            self.session.scalars(
                select(CanvasWriteReceipt)
                .where(CanvasWriteReceipt.canvas_id == canvas_id)
                .with_for_update()
            )
        )
        for receipt in receipts:
            if "$archive_document" in receipt.result_json:
                receipt.result_json = {
                    key: value
                    for key, value in receipt.result_json.items()
                    if key != "$archive_document"
                }
