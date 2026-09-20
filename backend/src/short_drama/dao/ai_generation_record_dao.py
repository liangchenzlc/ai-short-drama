from sqlalchemy import select
from sqlalchemy.orm import defer

from short_drama.domain import AIGenerationRecord

from .base import BaseDAO


class AIGenerationRecordDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, AIGenerationRecord)

    def for_task(self, task_id, *, include_text=True):
        statement = (
            select(AIGenerationRecord)
            .where(AIGenerationRecord.task_id == task_id)
            .order_by(AIGenerationRecord.call_no)
        )
        if not include_text:
            statement = statement.options(defer(AIGenerationRecord.text_content))
        return list(self.session.scalars(statement))
