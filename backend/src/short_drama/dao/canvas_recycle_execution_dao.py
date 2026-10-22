"""处置作者的终态执行载荷；保留幂等身份、公开回执与媒体出处。"""

from sqlalchemy import delete, or_, select, union
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    Base,
    CanvasLocalJob,
    CanvasRenderJob,
    CanvasResult,
    CanvasTaskBinding,
    CanvasToolRun,
    CanvasToolSubmission,
    MediaAsset,
    ProjectCanvas,
)
from short_drama.domain.canvas import CanvasWriteReceipt
from short_drama.service.base import utcnow

DISPOSED_ERROR = {"code": "canvas_work_disposed", "message": "原画布作品已处置，执行载荷已回收。"}
USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "input_characters",
    "audio_duration_ms",
}
ARCHIVE_FIELDS = {
    "asset_id",
    "output_index",
    "media_type",
    "locator",
    "checksum",
    "ext",
    "mime",
    "byte_size",
    "width",
    "height",
    "duration_ms",
    "saved",
    "ignored",
}


class CanvasRecycleExecutionDAO:
    def __init__(self, session: Session):
        if session.info.get("actor"):
            raise ValueError("Execution cleanup requires an internal maintenance session")
        self.session = session

    def task_ids(self, canvas_id: int) -> list[int]:
        roots = union(
            select(CanvasTaskBinding.async_task_id).where(CanvasTaskBinding.canvas_id == canvas_id),
            select(CanvasToolSubmission.async_task_id).where(
                CanvasToolSubmission.canvas_id == canvas_id,
                CanvasToolSubmission.async_task_id.is_not(None),
            ),
        )
        known = set(
            self.session.scalars(
                select(AsyncTask.id)
                .where(AsyncTask.id.in_(roots))
                .order_by(AsyncTask.id)
                .with_for_update()
            )
        )
        frontier = known
        # 通用重试可能没有画布绑定；沿实际重试外键取当前读，不能漏掉未结束的后代。
        while frontier:
            children = set(
                self.session.scalars(
                    select(AsyncTask.id)
                    .where(AsyncTask.retry_of_id.in_(frontier))
                    .order_by(AsyncTask.id)
                    .with_for_update()
                )
            )
            frontier = children - known
            known.update(frontier)
        return sorted(known)

    def _referenced(self, target: str, identifiers: list[int], ignored: set[str]) -> bool:
        if not identifiers:
            return False
        for table in Base.metadata.sorted_tables:
            if table.name in ignored:
                continue
            fields = [key.parent for key in table.foreign_keys if key.target_fullname == target]
            if (
                fields
                and self.session.scalar(
                    select(1)
                    .select_from(table)
                    .where(or_(*(field.in_(identifiers) for field in fields)))
                    .limit(1)
                    .with_for_update()
                )
                is not None
            ):
                return True
        return False

    def _protected(
        self, task: AsyncTask, records: list[AIGenerationRecord], *, ignored: set[str] | None = None
    ) -> bool:
        ignored = ignored or set()
        if self._referenced(
            "async_tasks.id",
            [task.id],
            {
                "async_tasks",
                "ai_generation_records",
                "canvas_task_bindings",
                "canvas_tool_submissions",
            }
            | ignored,
        ) or self._referenced(
            "ai_generation_records.id",
            [record.id for record in records],
            {"media_assets", "canvas_task_text_deltas"} | ignored,
        ):
            return True
        assets = list(
            self.session.scalars(
                select(MediaAsset.id)
                .where(MediaAsset.record_id.in_([record.id for record in records]))
                .with_for_update()
            )
        )
        return self._referenced("media_assets.id", assets, set())

    @staticmethod
    def _archive(response: dict | None) -> dict:
        response = response or {}
        retained = {
            "usage": {
                key: value
                for key, value in (response.get("usage") or {}).items()
                if key in USAGE_FIELDS and type(value) in (int, float)
            }
        }
        if "media_manifest" in response:
            retained["media_manifest"] = [
                {
                    key: value
                    for key, value in item.items()
                    if key in ARCHIVE_FIELDS and type(value) in (str, int, float, bool)
                }
                for item in response["media_manifest"]
            ]
        if type(response.get("expected_count")) is int:
            retained["expected_count"] = response["expected_count"]
        return retained

    def _bindings(self, canvas: ProjectCanvas, actor_id: int, task_id: int) -> None:
        bindings = list(
            self.session.scalars(
                select(CanvasTaskBinding)
                .where(
                    CanvasTaskBinding.canvas_id == canvas.id,
                    CanvasTaskBinding.project_id == canvas.project_id,
                    CanvasTaskBinding.initiated_by == actor_id,
                    CanvasTaskBinding.async_task_id == task_id,
                )
                .with_for_update()
            )
        )
        for binding in bindings:
            binding.source_snapshot = {}
            for name in ("canvas_task_text_deltas", "canvas_task_media_references"):
                table = Base.metadata.tables[name]
                self.session.execute(
                    delete(table).where(
                        table.c.task_binding_id == binding.id, table.c.created_by == actor_id
                    )
                )
            results = list(
                self.session.scalars(
                    select(CanvasResult)
                    .where(
                        CanvasResult.task_binding_id == binding.id,
                        CanvasResult.created_by == actor_id,
                    )
                    .with_for_update()
                )
            )
            for result in results:
                # 媒体索引保留规范资源和回执出处；正文副本不参与身份与重放。
                if result.kind == "text":
                    self.session.delete(result)
                else:
                    result.content_json = {}

    def _tools(self, canvas: ProjectCanvas, actor_id: int, cleared: set[int]) -> None:
        runs = list(
            self.session.scalars(
                select(CanvasToolRun)
                .where(CanvasToolRun.canvas_id == canvas.id, CanvasToolRun.user_id == actor_id)
                .order_by(CanvasToolRun.id)
                .with_for_update()
            )
        )
        for run in runs:
            items = list(
                self.session.scalars(
                    select(CanvasToolSubmission)
                    .where(CanvasToolSubmission.run_id == run.id)
                    .order_by(CanvasToolSubmission.id)
                    .with_for_update()
                )
            )
            if any(
                item.user_id != actor_id
                or item.async_task_id is not None
                and item.async_task_id not in cleared
                for item in items
            ):
                continue
            run.state_json = run.source_snapshot = {}
            run.source_media_id = None
            for item in items:
                item.request_json = item.execution_json = {}

    @staticmethod
    def pending_local(
        project_id: int | ColumnElement[int],
        canvas_id: int | ColumnElement[int],
        actor_id: int | ColumnElement[int],
    ) -> ColumnElement[bool]:
        return or_(
            *(
                select(model.id)
                .where(
                    model.project_id == project_id,
                    model.canvas_id == canvas_id,
                    model.user_id == actor_id,
                    model.created_by == actor_id,
                    model.disposal_receipt_id.is_(None),
                    model.error_json["code"].as_string() == "canvas_work_disposed",
                )
                .exists()
                for model in (CanvasLocalJob, CanvasRenderJob)
            )
        )

    def _local_jobs(self, canvas: ProjectCanvas, disposal: CanvasWriteReceipt) -> None:
        actor_id = disposal.actor_user_id
        for model in (CanvasLocalJob, CanvasRenderJob):
            jobs = list(
                self.session.scalars(
                    select(model)
                    .where(
                        model.canvas_id == canvas.id,
                        model.project_id == canvas.project_id,
                        model.user_id == actor_id,
                        model.created_by == actor_id,
                    )
                    .order_by(model.id)
                    .with_for_update()
                )
            )
            for job in jobs:
                if self._referenced(
                    model.__tablename__ + ".id", [job.id], {"canvas_render_media_references"}
                ):
                    continue
                job.request_json = job.source_snapshot = {}
                staging = (
                    (job.result_json or {}).get("playback_staging")
                    if isinstance(job, CanvasLocalJob)
                    else None
                )
                job.result_json = (
                    {"playback_staging": staging}
                    if staging
                    else {}
                    if job.result_json is not None
                    else None
                )
                job.error_json = dict(DISPOSED_ERROR)
                job.disposal_receipt_id = disposal.id
                job.output_media_id = None
                if isinstance(job, CanvasLocalJob):
                    job.source_media_id = None
                if isinstance(job, CanvasRenderJob):
                    job.plan_json = {}
                    table = Base.metadata.tables["canvas_render_media_references"]
                    self.session.execute(
                        delete(table).where(table.c.job_id == job.id, table.c.user_id == actor_id)
                    )

    def clear(self, canvas: ProjectCanvas, disposal: CanvasWriteReceipt) -> None:
        if (
            canvas.archived_at is None
            or disposal.operation_kind != "canvas.recycle.purge"
            or disposal.project_id != canvas.project_id
            or disposal.canvas_id != canvas.id
            or disposal.expected_row_version != canvas.row_version
            or disposal.result_json.get("source_key") != canvas.source_key
        ):
            raise ValueError("Execution cleanup requires the exact disposed canvas")
        actor_id = disposal.actor_user_id
        cleared = set()
        for identifier in self.task_ids(canvas.id):
            task = self.session.get(AsyncTask, identifier)
            if (
                task.initiated_by != actor_id
                or task.project_id != canvas.project_id
                or task.scope_user_id is not None
            ):
                continue
            records = list(
                self.session.scalars(
                    select(AIGenerationRecord)
                    .where(AIGenerationRecord.task_id == identifier)
                    .order_by(AIGenerationRecord.call_no)
                    .with_for_update()
                )
            )
            if not records or any(
                (record.request_data.get("source") or {}).get("scene")
                not in {"canvas_node", "canvas_tool"}
                or (record.request_data.get("source") or {}).get("canvas_id") != str(canvas.id)
                or (record.request_data.get("source") or {}).get("project_id")
                != str(canvas.project_id)
                or record.config_snapshot.get("agent_managed")
                or record.request_data.get("batch_id")
                for record in records
            ):
                continue
            if self._protected(task, records):
                continue
            self._bindings(canvas, actor_id, identifier)
            for record in records:
                source = record.request_data["source"]
                record.request_data = {
                    "source": {
                        key: source[key]
                        for key in (
                            "scene",
                            "project_id",
                            "canvas_id",
                            "node_key",
                            "source_node_key",
                        )
                        if key in source
                    }
                }
                record.config_snapshot = {
                    key: value
                    for key, value in record.config_snapshot.items()
                    if key in {"id", "name", "model_key", "provider", "row_version", "service_type"}
                    and type(value) in (str, int)
                }
                record.response_data = self._archive(record.response_data)
                record.text_content = record.credential_cipher = record.error = None
            task.error = dict(DISPOSED_ERROR)
            task.next_action = task.next_run_at = None
            task.message_status = "idle"
            task.message_version += 1
            task.updated_at = utcnow()
            cleared.add(identifier)
        self._tools(canvas, actor_id, cleared)
        self._local_jobs(canvas, disposal)
