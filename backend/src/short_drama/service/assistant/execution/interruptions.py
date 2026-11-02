"""Close interrupted orchestration receipts without cancelling admitted business work."""

from copy import deepcopy

from short_drama.agent.task.queries import task_for_run
from short_drama.agent.task.state import task_event
from short_drama.core.time import utcnow
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation


def settle_interrupted_locked(session, conversation, run, code):
    task = task_for_run(session, run)
    for operation in AgentTaskDAO(session).active_operations(task.id):
        if operation.run_id != run.id:
            continue
        receipt = (
            session.get(BusinessToolInvocation, operation.business_invocation_id)
            if operation.business_invocation_id
            else None
        )
        committed = receipt is not None and receipt.status == "succeeded"
        display = {
            key: value
            for key, value in (operation.result or {}).items()
            if key in {"_display", "_trace_bindings"}
        }
        operation.status = "succeeded" if committed else "failed"
        operation.error = (
            None if committed else {"code": "agent_cancelled" if operation.wait_state else code}
        )
        operation.result = (
            {
                **deepcopy(receipt.result),
                "business_invocation_id": str(receipt.id),
                "receipt_operation_id": str(operation.id),
                **display,
            }
            if committed
            else {**(operation.result or {}), "error": operation.error}
        )
        if committed:
            operation.result_refs, operation.changes = (
                deepcopy(receipt.result_refs),
                deepcopy(receipt.changes),
            )
        operation.phase = "terminal"
        operation.finished_at = operation.updated_at = utcnow()
        operation.row_version += 1
        call = session.get(AgentToolCall, operation.tool_call_id)
        if call is not None:
            call.status, call.error, call.updated_at = (
                "cancelled" if operation.wait_state and not committed else operation.status,
                operation.error,
                utcnow(),
            )
        if operation.wait_state:
            from short_drama.agent.core.events import append_event

            append_event(
                session,
                conversation,
                "question.cancelled"
                if operation.wait_state["kind"] == "user_input"
                else "wait.cancelled",
                {"operation_id": str(operation.id), "scope_id": str(operation.scope_id)},
                run_id=run.id,
            )
        task_event(
            session,
            conversation,
            task,
            "step.finished",
            run=run,
            operation_id=str(operation.id),
            tool_name=operation.tool_name,
            item_seq=operation.item_seq,
            outcome=operation.status,
            trace_version=str(operation.row_version),
            error=operation.error,
        )
    session.flush()
