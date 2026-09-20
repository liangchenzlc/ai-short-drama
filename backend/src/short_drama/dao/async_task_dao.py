from sqlalchemy import func, select
from sqlalchemy.orm import defer

from short_drama.core.exceptions import BusinessError
from short_drama.domain import AIGenerationRecord, AsyncTask

from .base import BaseDAO


def source_conditions(record, filters):
    scene, identifier = filters.get("source_scene"), filters.get("source_id")
    if scene is not None and scene != "shot_image":
        raise BusinessError("Unsupported source scene")
    if identifier is not None and scene is None:
        raise BusinessError("source_id requires source_scene")
    conditions = []
    if scene:
        conditions.append(record.request_data["source"]["scene"].as_string() == scene)
    if identifier:
        conditions.append(record.request_data["source"]["shot_id"].as_string() == str(identifier))
    return conditions


class AsyncTaskDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, AsyncTask)

    def by_key(self, key):
        return self.session.scalar(select(AsyncTask).where(AsyncTask.idempotency_key == key))

    def history(self, filters, offset, limit):
        conditions = source_conditions(AIGenerationRecord, filters)
        for key in ("service_type", "status"):
            if filters.get(key) is not None:
                conditions.append(getattr(AsyncTask, key) == filters[key])
        if filters.get("config_id"):
            conditions.append(AIGenerationRecord.config_id == int(filters["config_id"]))
        if filters.get("created_after"):
            conditions.append(AsyncTask.created_at >= filters["created_after"])
        if filters.get("created_before"):
            conditions.append(AsyncTask.created_at <= filters["created_before"])
        statement = (
            select(AsyncTask, AIGenerationRecord)
            .options(defer(AIGenerationRecord.text_content))
            .join(
                AIGenerationRecord,
                (AIGenerationRecord.task_id == AsyncTask.id) & (AIGenerationRecord.call_no == 1),
            )
            .where(*conditions)
        )
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(
            statement.order_by(AsyncTask.created_at.desc(), AsyncTask.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return rows, total
