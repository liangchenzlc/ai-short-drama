from sqlalchemy import func, select

from short_drama.domain import AIGenerationRecord, MediaAsset, MediaFile

from .async_task_dao import source_conditions
from .base import BaseDAO


class MediaAssetDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, MediaAsset)

    def history(self, filters, offset, limit):
        conditions = source_conditions(AIGenerationRecord, filters)
        if filters.get("media_type"):
            conditions.append(MediaAsset.media_type == filters["media_type"])
        if filters.get("name"):
            conditions.append(MediaAsset.name.contains(filters["name"], autoescape=True))
        for key, op in (
            ("created_after", MediaAsset.created_at.__ge__),
            ("created_before", MediaAsset.created_at.__le__),
        ):
            if filters.get(key):
                conditions.append(op(filters[key]))
        statement = (
            select(MediaAsset, AIGenerationRecord, MediaFile)
            .join(AIGenerationRecord, AIGenerationRecord.id == MediaAsset.record_id)
            .join(MediaFile, MediaFile.id == MediaAsset.media_id)
            .where(*conditions)
        )
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(
            statement.order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return rows, total
