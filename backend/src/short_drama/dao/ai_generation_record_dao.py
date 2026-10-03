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

    def for_tasks(self, task_ids, *, include_text=False):
        """Read only this page's call history, preserving per-task call order."""
        task_ids = list(task_ids)
        by_task = {identifier: [] for identifier in task_ids}
        if not task_ids:
            return by_task
        statement = (
            select(AIGenerationRecord)
            .where(AIGenerationRecord.task_id.in_(task_ids))
            .order_by(AIGenerationRecord.task_id, AIGenerationRecord.call_no)
        )
        if not include_text:
            statement = statement.options(defer(AIGenerationRecord.text_content))
        for record in self.session.scalars(statement):
            by_task[record.task_id].append(record)
        return by_task
