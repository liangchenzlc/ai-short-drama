from datetime import datetime

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, Select

from short_drama.domain import (
    CanvasBinaryResource,
    CanvasCreationAttempt,
    CanvasCreationResource,
    CanvasResourceUpload,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.canvas import CanvasWriteReceipt
from short_drama.service.base import utcnow


class CanvasCreationDAO:
    def __init__(self, session: Session):
        self.session = session

    def by_key(self, user_id: int, key: str, *, lock: bool = True):
        query = select(CanvasCreationAttempt).where(
            CanvasCreationAttempt.user_id == user_id,
            CanvasCreationAttempt.idempotency_key == key,
        )
        return self.session.scalar(self._lock(query) if lock else query)

    def attempt(self, identifier: int, *, lock: bool = True):
        query = select(CanvasCreationAttempt).where(CanvasCreationAttempt.id == identifier)
        return self.session.scalar(self._lock(query) if lock else query)

    def resources(self, attempt_id: int, *, lock: bool = True):
        query = (
            select(CanvasCreationResource)
            .where(CanvasCreationResource.attempt_id == attempt_id)
            .order_by(CanvasCreationResource.source_resource_id)
        )
        return list(self.session.scalars(self._lock(query) if lock else query))

    def pending_count(self, user_id: int, now: datetime) -> int:
        return (
            self.session.scalar(
                select(func.count(CanvasCreationAttempt.id)).where(
                    CanvasCreationAttempt.user_id == user_id,
                    CanvasCreationAttempt.status == "pending",
                    CanvasCreationAttempt.expires_at > now,
                )
            )
            or 0
        )

    def expired(self, now: datetime, limit: int, *, after_id: int = 0) -> list[int]:
        return list(
            self.session.scalars(
                select(CanvasCreationAttempt.id)
                .where(
                    CanvasCreationAttempt.id > after_id,
                    CanvasCreationAttempt.status == "pending",
                    CanvasCreationAttempt.expires_at <= now,
                )
                .order_by(CanvasCreationAttempt.id)
                .limit(limit)
            )
        )

    @staticmethod
    def disposed_query(
        project_id: int | ColumnElement[int],
        canvas_id: int | ColumnElement[int],
        source_key: str | ColumnElement[str],
        user_id: int | ColumnElement[int],
        *,
        include_cleaned: bool = False,
    ) -> Select[tuple[CanvasCreationAttempt]]:
        # 准备记录没有canvas_id；只能沿服务端冻结的创建结果核对目标。
        query = select(CanvasCreationAttempt).where(
            CanvasCreationAttempt.user_id == user_id,
            CanvasCreationAttempt.created_by == user_id,
            CanvasCreationAttempt.source_key == source_key,
            CanvasCreationAttempt.status == "ready",
            CanvasCreationAttempt.result_json["project_id"].as_string() == cast(project_id, String),
            CanvasCreationAttempt.result_json["id"].as_string() == cast(canvas_id, String),
            CanvasCreationAttempt.result_json["source_key"].as_string() == source_key,
            or_(
                and_(
                    CanvasCreationAttempt.operation_kind == "canvas.workspace.create",
                    CanvasCreationAttempt.target_project_id.is_(None),
                ),
                and_(
                    CanvasCreationAttempt.operation_kind == "canvas.create",
                    CanvasCreationAttempt.target_project_id == project_id,
                ),
            ),
        )
        return (
            query
            if include_cleaned
            else query.where(func.json_length(CanvasCreationAttempt.request_json) > 0)
        )

    @classmethod
    def pending_legacy_media(
        cls,
        project_id: int | ColumnElement[int],
        canvas_id: int | ColumnElement[int],
        source_key: str | ColumnElement[str],
        user_id: int | ColumnElement[int],
    ) -> ColumnElement[bool]:
        prepared = CanvasCreationResource
        remaining = select(prepared.id).where(
            prepared.attempt_id == CanvasCreationAttempt.id,
            prepared.user_id == user_id,
            prepared.created_by == user_id,
            ~select(CanvasResourceUpload.id)
            .where(CanvasResourceUpload.reserved_resource_id == prepared.target_resource_id)
            .exists(),
            or_(
                select(MediaFile.id).where(MediaFile.id == prepared.target_resource_id).exists(),
                select(CanvasBinaryResource.id)
                .where(CanvasBinaryResource.id == prepared.target_resource_id)
                .exists(),
            ),
        )
        return (
            cls.disposed_query(project_id, canvas_id, source_key, user_id, include_cleaned=True)
            .where(remaining.exists())
            .exists()
        )

    def _disposed_preparations(
        self, canvas: ProjectCanvas, disposal: CanvasWriteReceipt, *, include_cleaned: bool
    ) -> list[tuple[CanvasCreationAttempt, list[CanvasCreationResource]]]:
        if self.session.info.get("actor"):
            raise ValueError("Creation cleanup requires an internal maintenance session")
        if (
            canvas.archived_at is None
            or disposal.operation_kind != "canvas.recycle.purge"
            or disposal.project_id != canvas.project_id
            or disposal.canvas_id != canvas.id
            or disposal.expected_row_version != canvas.row_version
            or disposal.result_json.get("source_key") != canvas.source_key
        ):
            raise ValueError("Creation cleanup requires the exact disposed canvas")
        attempts = self.session.scalars(
            self._lock(
                self.disposed_query(
                    canvas.project_id,
                    canvas.id,
                    canvas.source_key,
                    disposal.actor_user_id,
                    include_cleaned=include_cleaned,
                ).order_by(CanvasCreationAttempt.id)
            )
        )
        preparations = []
        for attempt in attempts:
            receipt = self.session.scalar(
                select(CanvasWriteReceipt)
                .where(
                    CanvasWriteReceipt.actor_user_id == attempt.user_id,
                    CanvasWriteReceipt.idempotency_key == attempt.idempotency_key,
                    CanvasWriteReceipt.operation_kind == attempt.operation_kind,
                    CanvasWriteReceipt.project_id == canvas.project_id,
                    CanvasWriteReceipt.canvas_id == canvas.id,
                    CanvasWriteReceipt.request_hash == attempt.request_hash,
                )
                .with_for_update()
            )
            if receipt is None or receipt.result_json != attempt.result_json:
                raise ValueError("Creation cleanup requires its unchanged public receipt")
            resources = self.resources(attempt.id)
            if any(
                row.status != "attached"
                or row.released_at is None
                or row.source_media_id is not None
                or row.source_binary_id is not None
                for row in resources
            ):
                raise ValueError("Completed creation still has an unfinished preparation")
            preparations.append((attempt, resources))
        return preparations

    def disposed_resources(
        self, canvas: ProjectCanvas, disposal: CanvasWriteReceipt
    ) -> list[CanvasCreationResource]:
        result = []
        for attempt, rows in self._disposed_preparations(canvas, disposal, include_cleaned=True):
            for row in rows:
                if (
                    row.user_id != disposal.actor_user_id
                    or row.created_by != disposal.actor_user_id
                    or attempt.result_json.get("resource_map", {}).get(str(row.source_resource_id))
                    != str(row.target_resource_id)
                ):
                    raise ValueError("Creation media cleanup requires its original author and map")
                result.append(row)
        return result

    def clear_disposed(self, canvas: ProjectCanvas, disposal: CanvasWriteReceipt) -> int:
        preparations = self._disposed_preparations(canvas, disposal, include_cleaned=False)
        for attempt, _ in preparations:
            # hash、身份、公开结果与资源出处保持；它们继续决定旧键重放和资源别名。
            attempt.request_json = {}
            attempt.updated_at = utcnow()
        return len(preparations)

    @staticmethod
    def _lock(query):
        return query.with_for_update().execution_options(populate_existing=True)
