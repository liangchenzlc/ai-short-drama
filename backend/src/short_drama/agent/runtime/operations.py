from copy import deepcopy

from pydantic import ValidationError
from sqlalchemy import select

from short_drama.agent.core.access import lock_run, may_decide
from short_drama.agent.core.events import TERMINAL
from short_drama.agent.core.fence import owns_execution as owns_execution
from short_drama.agent.core.items import allocate_item_seq
from short_drama.agent.core.state import finish_locked, mark_scheduled, wait_locked
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.runtime.tool_execution import execute_locked
from short_drama.agent.skill.contracts import (
    SKILL_LOAD,
    SKILL_RESOURCE,
    SKILL_SEARCH,
    activation_summary,
    activations,
)
from short_drama.agent.skill.runtime import (
    prepare_resource_read,
)
from short_drama.agent.task.controls import apply_controls_locked
from short_drama.agent.task.queries import task_for_run
from short_drama.agent.task.state import apply_inputs_locked, task_event
from short_drama.agent.tools.contracts import (
    CANVAS_TOOL,
    IMAGE_CONTENT_TOOL,
    IMAGE_PRIMARY_TOOL,
    IMAGE_READ_TOOL,
    MEDIA_STATUS_TOOL,
    MEDIA_TOOL,
    MEMORY_WRITE_TOOLS,
    STANDARD_READ_TOOL,
    STANDARD_TOOL,
)
from short_drama.agent.tools.contracts import MEMORY_READ_LIMIT as MEMORY_READ_LIMIT
from short_drama.agent.tools.contracts import TOOL_SCHEMAS as TOOL_SCHEMAS
from short_drama.agent.tools.contracts import DescribeTarget as DescribeTarget
from short_drama.agent.tools.contracts import MemoryCatalog as MemoryCatalog
from short_drama.agent.tools.contracts import ReadHistory as ReadHistory
from short_drama.agent.tools.contracts import ReadMemory as ReadMemory
from short_drama.agent.tools.contracts import ReadReceipt as ReadReceipt
from short_drama.agent.tools.contracts import ReadSelectedSubjects as ReadSelectedSubjects
from short_drama.agent.tools.contracts import SearchMemory as SearchMemory
from short_drama.agent.tools.contracts import tool_manifest as tool_manifest
from short_drama.agent.tools.presentation import label as tool_label
from short_drama.agent.tools.presentation import visibility as tool_visibility
from short_drama.core.exceptions import BusinessError
from short_drama.dao.assistant.execution.transcript import AgentTranscriptDAO
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain import Project
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.service.assistant.memory.memory_store import (
    lock_owner_conversations,
)
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


def claim_operation(factory, settings, run_id, *, fence=None):
    with factory.begin() as session:
        rows = lock_run(session, run_id)
        if rows is None:
            return None
        project, conversation, run = rows
        if run.status in TERMINAL or run.phase != "tools" or not owns_execution(run, fence):
            return None
        task = task_for_run(session, run)
        dao = AgentTaskDAO(session)
        if run.checkpoint.get("discussion_revision"):
            finish_locked(
                session, conversation, run, "failed", {"code": "assistant_tools_forbidden"}
            )
            return None
        active = dao.active_operation(task.id)
        if active is not None:
            if active.status in {"waiting_media", "reconciling"}:
                wait_locked(session, conversation, run, "waiting_generation")
                return None
            # Recover the same durable operation. A new delivery version fences
            # the former worker without creating another business operation.
            active.execution_epoch = run.message_version
            return active.id
        if not settings.agent_enabled or not may_decide(session, project, conversation, run):
            finish_locked(session, conversation, run, "cancelled", {"code": "access_revoked"})
            return None
        apply_controls_locked(session, conversation, task, run)
        if run.status in TERMINAL or run.cancel_requested:
            return None
        if task.input_revision != task.applied_input_revision:
            apply_inputs_locked(session, conversation, task, run)
            mark_scheduled(run, phase="model")
            return None
        turn = AgentTranscriptDAO(session).latest_turn(run.id)
        if turn is None or not (turn.response or {}).get("applied"):
            finish_locked(
                session, conversation, run, "failed", {"code": "agent_response_not_durable"}
            )
            return None
        calls = session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.turn_id == turn.id)
            .order_by(AgentToolCall.call_index)
            .with_for_update()
        ).all()
        call = next((item for item in calls if item.status in {"prepared", "ready"}), None)
        if call is None:
            checkpoint = deepcopy(run.checkpoint)
            checkpoint["pending_results"] = {
                "calls": {
                    item.provider_call_id: item.result or {"error": (item.error or {}).get("code")}
                    for item in calls
                }
            }
            run.checkpoint = checkpoint
            task.phase = "deciding"
            mark_scheduled(run, phase="model")
            return None
        arguments = call.arguments.get("parsed")
        methods = [
            activation_summary(item)
            for item in activations(task)
            if item["active"] and not item.get("issue")
        ]
        operation = AgentOperation(
            id=next_id(),
            task_id=task.id,
            run_id=run.id,
            tool_call_id=call.id,
            tool_name=call.tool_name,
            item_seq=allocate_item_seq(conversation),
            tool_version=(
                turn.request_messages[0].get("kwargs", {}).get("tool_version", 2)
                if (run.checkpoint or {}).get("engine") == ENGINE_VERSION
                else 1
            ),
            plan_revision=task.plan_revision,
            input_revision=task.applied_input_revision,
            payload_hash=digest(arguments),
            arguments=arguments if isinstance(arguments, dict) else {"invalid_arguments": True},
            status="running",
            phase="tool_running",
            row_version=1,
            execution_epoch=run.message_version,
            result_refs=[],
            result={"skill_activations": methods} if methods else None,
            changes=[],
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        call.status = "ready"
        task.phase = "tool_running"
        task.row_version += 1
        session.add(operation)
        session.flush()
        task_event(
            session,
            conversation,
            task,
            "step.started",
            run=run,
            call_id=str(call.id),
            operation_id=str(operation.id),
            tool_name=operation.tool_name,
            item_seq=operation.item_seq,
            visibility=tool_visibility(operation.tool_name),
            label=tool_label(operation.tool_name),
            phase=operation.phase,
            trace_version=str(operation.row_version),
            skill_activations=methods,
        )
        return operation.id


def _abandon_operation_locked(session, conversation, run, operation_id, fence):
    """Converge an unsettled operation after its delivery lost the lease.

    The business effect and its receipt share one transaction, so an operation
    still ``running`` here never committed. Marking it failed lets recovery
    re-decide instead of leaving the task waiting on a phantom active row.
    """
    if fence is None or run.message_version != fence[0] or run.status in TERMINAL:
        return False
    operation = session.get(AgentOperation, operation_id)
    if operation is None or operation.run_id != run.id or operation.status != "running":
        return False
    error = {
        "code": "agent_lease_lost_operation",
        "message": "执行租约已失效，本次工具调用未结算，将重新决策",
        "retryable": True,
    }
    operation.result = {"error": error}
    operation.error = error
    operation.status, operation.phase = "failed", "terminal"
    operation.row_version += 1
    operation.updated_at = operation.finished_at = utcnow()
    call = session.get(AgentToolCall, operation.tool_call_id)
    if call is not None:
        call.status, call.error, call.updated_at = "failed", error, utcnow()
        call.result = {"codec": "agent.operation-ref", "operation_id": str(operation.id)}
    task = task_for_run(session, run)
    if task is not None:
        task.phase = "at_boundary"
        task.row_version += 1
        task.updated_at = utcnow()
        task_event(
            session,
            conversation,
            task,
            "step.requeued",
            run=run,
            call_id=str(operation.tool_call_id),
            operation_id=str(operation.id),
            tool_name=operation.tool_name,
            error=error,
        )
    return True


def complete_operation(factory, settings, run_id, operation_id, *, fence=None):
    resource_prepared = prepare_resource_read(factory, settings, run_id, operation_id, fence)
    with factory.begin() as session:
        tool = session.scalar(
            select(AgentOperation.tool_name).where(
                AgentOperation.id == operation_id, AgentOperation.run_id == run_id
            )
        )
        if tool == MEDIA_TOOL:
            from short_drama.domain.assistant.tasks.task import AgentTask

            target = session.scalar(
                select(AgentTask.execution_target)
                .join(
                    AgentOperation,
                    AgentOperation.task_id == AgentTask.id,
                )
                .where(AgentOperation.id == operation_id, AgentOperation.run_id == run_id)
            )
            if target and target.get("project_id"):
                session.scalar(
                    select(Project).where(Project.id == int(target["project_id"])).with_for_update()
                )
        if tool in MEMORY_WRITE_TOOLS:
            owner = session.scalar(select(AgentRun.initiated_by).where(AgentRun.id == run_id))
            if owner is not None:
                lock_owner_conversations(session, owner, include_deleting=True)
        rows = lock_run(session, run_id)
        if rows is None:
            return
        _project, conversation, run = rows
        if not owns_execution(run, fence):
            _abandon_operation_locked(session, conversation, run, operation_id, fence)
            return
        if tool == SKILL_RESOURCE and (
            not resource_prepared
            or resource_prepared["fence"] is None
            or not owns_execution(run, resource_prepared["fence"])
        ):
            return
        task = task_for_run(session, run)
        operation = session.scalar(
            select(AgentOperation)
            .where(
                AgentOperation.id == operation_id,
                AgentOperation.task_id == task.id,
                AgentOperation.run_id == run.id,
            )
            .with_for_update()
        )
        if operation is None or operation.status in {
            "succeeded",
            "failed",
            "conflict",
            "waiting_media",
            "reconciling",
        }:
            return
        if fence is not None and operation.execution_epoch != fence[0]:
            return
        call = session.get(AgentToolCall, operation.tool_call_id)
        try:
            with session.begin_nested():
                result = execute_locked(
                    session,
                    conversation,
                    task,
                    operation,
                    settings=settings,
                    resource_prepared=resource_prepared,
                )
                session.flush()
            if (operation.result or {}).get("skill_activations"):
                result = {**result, "skill_activations": operation.result["skill_activations"]}
            if operation.tool_name == MEDIA_TOOL and not result.get("settled", True):
                persist_tool_result(run, operation, call, result)
                operation.status, operation.phase = "waiting_media", "waiting_media"
                operation.row_version += 1
                operation.updated_at = utcnow()
                call.status, call.updated_at = "waiting_generation", utcnow()
                task.status, task.phase = "waiting_media", "waiting_media"
                task.row_version += 1
                wait_locked(session, conversation, run, "waiting_generation")
                task_event(
                    session,
                    conversation,
                    task,
                    "step.waiting_media",
                    run=run,
                    call_id=str(call.id),
                    operation_id=str(operation.id),
                    tool_name=MEDIA_TOOL,
                    phase=operation.phase,
                    trace_version=str(operation.row_version),
                )
                return
            if operation.tool_name == MEDIA_TOOL and (
                result.get("error")
                or not result.get("already_applied")
                and any(item["status"] != "succeeded" for item in result.get("items", []))
            ):
                result["error"] = {
                    "code": "assistant_media_batch_failed",
                    "message": "媒体批次未全部受理，未外发项已释放预占",
                }
                operation.status, call.status = "failed", "failed"
            else:
                operation.status, call.status = "succeeded", "succeeded"
        except (ValueError, ValidationError):
            result = {"error": {"code": "invalid_tool_arguments", "message": "工具参数无效"}}
            operation.status, call.status = "failed", "failed"
        except BusinessError as error:
            result = {"error": {"code": error.code, "message": error.message}}
            if (
                operation.tool_name in {STANDARD_TOOL, CANVAS_TOOL}
                and error.code == "assistant_delete_impact"
                or (run.checkpoint or {}).get("engine") == ENGINE_VERSION
                and error.code == "invalid_tool_arguments"
            ):
                result["error"]["details"] = error.details
            operation.status, call.status = (
                ("conflict", "failed")
                if operation.tool_name in {STANDARD_TOOL, CANVAS_TOOL, IMAGE_PRIMARY_TOOL}
                and error.status_code == 409
                else ("failed", "failed")
            )
        finish_operation_locked(session, conversation, task, run, operation, call, result)


def persist_tool_result(run, operation, call, result):
    harness = (run.checkpoint or {}).get("engine") == ENGINE_VERSION
    if harness:
        from short_drama.agent.resources.contracts import assert_reference_payload

        result = {**result, "receipt_operation_id": str(operation.id)}
        assert_reference_payload(result)
    operation.result = result
    call.result = (
        {"codec": "agent.operation-ref", "operation_id": str(operation.id)} if harness else result
    )


def finish_operation_locked(session, conversation, task, run, operation, call, result):
    if (operation.result or {}).get("skill_activations"):
        result = {**result, "skill_activations": operation.result["skill_activations"]}
    harness = (run.checkpoint or {}).get("engine") == ENGINE_VERSION
    persist_tool_result(run, operation, call, result)
    operation.error = result.get("error")
    operation.phase = "terminal"
    operation.row_version += 1
    operation.updated_at = operation.finished_at = utcnow()
    call.error, call.updated_at = operation.error, utcnow()
    session.flush()
    if harness:
        from short_drama.agent.harness.supervision import evidence_kind, record_progress
        from short_drama.agent.tools.digest import result_digest

        operation.result_digest = result_digest(operation.tool_name, result)
        operation.evidence_kind = evidence_kind(operation.tool_name, result)
        record_progress(session, conversation, task, run, operation, result)
    task.phase = "at_boundary"
    task.row_version += 1
    task_event(
        session,
        conversation,
        task,
        "step.finished",
        run=run,
        call_id=str(call.id),
        operation_id=str(operation.id),
        tool_name=operation.tool_name,
        item_seq=operation.item_seq,
        visibility=tool_visibility(operation.tool_name),
        label=tool_label(operation.tool_name),
        phase="terminal",
        outcome=operation.status,
        trace_version=str(operation.row_version),
        result_refs=operation.result_refs,
        error=operation.error,
        skill_activations=result.get("skill_activations", []),
    )
    if not harness:
        apply_controls_locked(session, conversation, task, run)
    if (
        not harness
        and operation.status != "succeeded"
        and operation.tool_name
        not in {
            "memory_catalog",
            "search_memory",
            "read_memory",
            "read_conversation_history",
            STANDARD_READ_TOOL,
            IMAGE_READ_TOOL,
            IMAGE_CONTENT_TOOL,
            MEDIA_STATUS_TOOL,
            SKILL_SEARCH,
            SKILL_LOAD,
            SKILL_RESOURCE,
        }
        and run.status not in TERMINAL
    ):
        finish_locked(session, conversation, run, "failed", operation.error)
