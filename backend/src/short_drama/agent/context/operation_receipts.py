from sqlalchemy import select

from short_drama.agent.context.delegation import delegated_inputs_locked, receipt_is_handed_off
from short_drama.agent.context.delegation_results import visible_business_outputs_locked
from short_drama.agent.task.state import read_operation
from short_drama.core.exceptions import NotFound
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.domain.assistant.undo.undo import AgentBatchUndo


def scoped_operation_locked(session, task, operation, source_id):
    scope = session.get(AgentExecutionScope, operation.scope_id)
    receipt = session.scalar(
        select(AgentOperation).where(
            AgentOperation.id == source_id,
            AgentOperation.task_id == task.id,
        )
    )
    if (
        receipt is None
        or (receipt.run_id != operation.run_id or receipt.scope_id != operation.scope_id)
        and not (
            receipt.scope_id == scope.parent_scope_id
            and receipt_is_handed_off(
                delegated_inputs_locked(session, operation.run_id, operation.scope_id), receipt
            )
        )
        and receipt.id
        not in {
            item.id
            for item in visible_business_outputs_locked(
                session, session.get(AgentRun, operation.run_id), operation.scope_id
            )
        }
    ):
        raise NotFound("当前执行范围中没有此操作回执")
    return receipt


def read_receipt_locked(session, conversation, task, operation, values):
    receipt = scoped_operation_locked(session, task, operation, values.operation_id)
    return {
        "receipt": read_operation(receipt).model_dump(mode="json"),
        "undo_receipts": [
            {
                "id": str(item.id),
                "selected_keys": item.selected_keys,
                "result_refs": item.result_refs,
            }
            for item in session.scalars(
                select(AgentBatchUndo)
                .where(AgentBatchUndo.operation_id == receipt.id)
                .order_by(AgentBatchUndo.id)
            )
        ],
    }
