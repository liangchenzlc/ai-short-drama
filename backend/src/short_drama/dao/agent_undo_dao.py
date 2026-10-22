"""Private undo persistence; the service owns conversation and business locks."""

from sqlalchemy import select

from short_drama.domain.agent_task import AgentOperation
from short_drama.domain.agent_undo import AgentBatchUndo


class AgentUndoDAO:
    def __init__(self, session):
        self.session = session

    def receipts(self, operation_id):
        return list(
            self.session.scalars(
                select(AgentBatchUndo)
                .where(AgentBatchUndo.operation_id == operation_id)
                .order_by(AgentBatchUndo.id)
                .with_for_update()
            )
        )

    def request(self, conversation_id, key):
        return self.session.scalar(
            select(AgentBatchUndo)
            .where(
                AgentBatchUndo.conversation_id == conversation_id, AgentBatchUndo.request_key == key
            )
            .with_for_update()
        )

    def task_receipts(self, conversation_id, task_id):
        return list(
            self.session.execute(
                select(AgentBatchUndo, AgentOperation)
                .join(AgentOperation, AgentOperation.id == AgentBatchUndo.operation_id)
                .where(
                    AgentBatchUndo.conversation_id == conversation_id,
                    AgentBatchUndo.task_id == task_id,
                    AgentOperation.task_id == task_id,
                )
                .order_by(AgentBatchUndo.id)
                .with_for_update()
            )
        )

    def add(self, **values):
        row = AgentBatchUndo(**values)
        self.session.add(row)
        self.session.flush()
        return row
