from sqlalchemy import select

from short_drama.agent.context.delegation import (
    delegated_inputs_locked,
    receipt_is_handed_off,
    scope_snapshot_locked,
)
from short_drama.agent.context.delegation_results import visible_business_outputs_locked
from short_drama.agent.context.tool_results import (
    SKILL_BODY_TOOLS,
    eligible_scope_keys,
    result_page,
    source_context_eligible,
)
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.task.state import read_operation
from short_drama.core.exceptions import NotFound
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
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


def read_receipt_locked(session, conversation, task, operation, values, *, state=None):
    receipt = scoped_operation_locked(session, task, operation, values.operation_id)
    result = {
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
    if values.include_result:
        result.update(
            _read_result_locked(session, conversation, task, operation, receipt, values, state)
        )
    return result


def _read_result_locked(session, conversation, task, operation, receipt, values, state):
    call = session.get(AgentToolCall, receipt.tool_call_id)
    context = (call.result or {}).get("source_context")
    restricted, _ = restricted_messages(session, conversation.id)
    scope_keys = eligible_scope_keys(
        session, conversation.owner_user_id, scope_snapshot_locked(session, task), restricted
    )
    run = session.get(AgentRun, operation.run_id)
    if (
        receipt.tool_name in SKILL_BODY_TOOLS
        or receipt.status != "succeeded"
        or (call.result or {}).get("codec") != "agent.graph-tool-result"
        or not source_context_eligible(
            context, run.checkpoint.get("memory_epoch", 0), restricted, scope_keys, state
        )
    ):
        raise NotFound("原始结果已不在当前可读取范围；方法正文与资料请使用对应方法工具")
    return {
        **{key: context[key] for key in ("source_message_ids", "skill_activation_ids")},
        "result_page": result_page(
            call.result["message"]["data"]["content"],
            values.character_offset,
            values.character_limit,
        ),
    }
