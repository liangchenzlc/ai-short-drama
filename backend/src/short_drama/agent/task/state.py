"""Task boundaries are serialized with input/control receipt and operation claim."""

from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import select

from short_drama.agent.context.subjects import subject_catalog, subject_memory_keys
from short_drama.agent.core import ENGINE_VERSION
from short_drama.agent.core.events import append_event, budget_limits, initial_usage
from short_drama.agent.runtime.trace_content import display_refs, display_safe
from short_drama.agent.task.queries import task_for_run as task_for_run
from short_drama.agent.tools.contracts import TOOL_VERSION
from short_drama.agent.tools.presentation import label as tool_label
from short_drama.agent.tools.trace_visibility import tool_trace_visibility
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.conversations.models import AgentEvent
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.undo.undo import AgentBatchUndo
from short_drama.schemas.assistant.tasks.task import OperationRead, TaskRead
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id


def read_operation(row, *, undo_receipts=(), event_seq=None, result_seq=None, turn_id=None):
    return OperationRead(
        **(row.result or {}).get("_display", {}),
        id=row.id,
        task_id=row.task_id,
        run_id=row.run_id,
        call_id=row.tool_call_id,
        turn_id=turn_id,
        tool_name=row.tool_name,
        item_seq=row.item_seq,
        event_seq=event_seq,
        result_seq=result_seq,
        trace_visibility=tool_trace_visibility(row.tool_name),
        label=tool_label(row.tool_name),
        tool_version=row.tool_version,
        plan_revision=row.plan_revision,
        state_version=row.row_version,
        phase=row.phase,
        outcome=row.status,
        result_refs=display_refs(row.result_refs),
        skill_activations=(row.result or {}).get("skill_activations", []),
        undo_receipts=list(undo_receipts),
        error=display_safe(row.error),
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def read_operations(session, rows):
    from short_drama.schemas.assistant.undo.undo import BatchUndoRead

    receipts = {}
    event_metadata = {}
    turn_ids = {}
    if rows:
        turn_ids = {
            str(call_id): str(turn_id)
            for call_id, turn_id in session.execute(
                select(AgentToolCall.id, AgentToolCall.turn_id).where(
                    AgentToolCall.id.in_([row.tool_call_id for row in rows])
                )
            )
        }
        for row in session.scalars(
            select(AgentBatchUndo)
            .where(AgentBatchUndo.operation_id.in_([row.id for row in rows]))
            .order_by(AgentBatchUndo.id)
        ):
            receipts.setdefault(row.operation_id, []).append(
                BatchUndoRead.model_validate(row, from_attributes=True).model_dump(mode="json")
            )
        for event_type, seq, payload in session.execute(
            select(AgentEvent.event_type, AgentEvent.seq, AgentEvent.payload)
            .where(
                AgentEvent.run_id.in_({row.run_id for row in rows}),
                AgentEvent.event_type.in_(["step.started", "step.finished"]),
                AgentEvent.payload["operation_id"].as_string().in_([str(row.id) for row in rows]),
            )
            .order_by(AgentEvent.seq)
        ):
            operation_id = payload.get("operation_id")
            if not operation_id:
                continue
            item = event_metadata.setdefault(str(operation_id), {})
            if event_type == "step.started":
                item.setdefault("event_seq", seq)
            else:
                item["result_seq"] = seq
    return [
        read_operation(
            row,
            undo_receipts=receipts.get(row.id, ()),
            turn_id=turn_ids.get(str(row.tool_call_id)),
            **event_metadata.get(str(row.id), {}),
        )
        for row in rows
    ]


_UNSET_OPERATION = object()


def task_capabilities(task):
    """Interruption support is per-engine and per-frozen-policy, never global."""
    from short_drama.agent.task.policy import policy

    stop = policy(task, "stop") == "boundary.v1"
    return {"stop": stop, "redirect": False, "resume": False}


def read_task(session, task, *, active_operation=_UNSET_OPERATION):
    operation = (
        next(iter(AgentTaskDAO(session).active_operations(task.id, lock=False)), None)
        if active_operation is _UNSET_OPERATION
        else active_operation
    )
    selected = subject_catalog(task.context_snapshot)
    sources = {(item["project_id"], item["source_kind"], item["source_id"]) for item in selected}
    return TaskRead(
        id=task.id,
        conversation_id=task.conversation_id,
        goal=task.goal,
        status=task.status,
        phase=task.phase,
        row_version=task.row_version,
        plan_revision=task.plan_revision,
        input_revision=task.input_revision,
        applied_input_revision=task.applied_input_revision,
        control_seq=task.control_seq,
        applied_control_seq=task.applied_control_seq,
        control_state=task.control_state,
        interruptions=task_capabilities(task),
        execution_target=task.execution_target,
        selected_subjects=selected,
        source_context={
            "kind": selected[0]["source_kind"],
            "id": selected[0]["source_id"],
            "revision": selected[0]["revision"],
            "project_id": selected[0]["project_id"],
        }
        if len(sources) == 1
        else None,
        skills=[
            {key: item[key] for key in ("id", "name", "content_version", "builtin") if key in item}
            for item in task.selected_skills
        ],
        skill_activations=[],
        media_usage=task.media_usage,
        active_operation=read_operation(operation) if operation else None,
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def task_event(session, conversation, task, event_type, *, run=None, **payload):
    return append_event(
        session,
        conversation,
        event_type,
        {
            "task_id": str(task.id),
            "state_version": str(task.row_version),
            "plan_revision": str(task.plan_revision),
            **payload,
        },
        run_id=run.id if run else None,
    )


def new_attempt_locked(
    session,
    conversation,
    task,
    message,
    model_id,
    snapshot,
    previous=None,
    *,
    trigger_revision=0,
    settings=None,
    tool_lookup=None,
):
    from short_drama.agent.skill.progressive import freeze_catalog_locked
    from short_drama.service.assistant.settings.preferences import freeze_run_settings_locked

    now = utcnow()
    frozen = freeze_run_settings_locked(
        session, conversation.owner_user_id, settings, tool_catalog_version=TOOL_VERSION
    )
    selected = task.selected_skills
    for item in AgentTaskDAO(session).inputs(task):
        selected = item.payload.get("selected_skills", selected)
    snapshot = {
        **deepcopy(snapshot),
        "assistant_settings": frozen,
        "skill_catalog": freeze_catalog_locked(
            session,
            conversation.owner_user_id,
            settings,
            tool_lookup=tool_lookup,
            selected=selected,
        ),
    }
    run = AgentRun(
        id=next_id(),
        conversation_id=conversation.id,
        task_id=task.id,
        trigger_message_id=message.id,
        trigger_revision=trigger_revision,
        initiated_by=conversation.owner_user_id,
        model_config_id=model_id,
        config_snapshot=deepcopy(snapshot),
        status="queued",
        phase="model",
        row_version=1,
        checkpoint_schema_version=1,
        checkpoint={
            "purpose": "global_assistant",
            "engine": ENGINE_VERSION,
            "task_input_revision": task.applied_input_revision,
            "task_plan_revision": task.plan_revision,
        },
        budget=budget_limits(
            "harness",
            caps={
                "decision_calls": frozen["model_call_limit"],
                "output_tokens": frozen["output_token_limit"],
            },
        ),
        usage=initial_usage(),
        message_status="pending",
        message_version=1,
        next_run_at=now,
        cancel_requested=0,
        created_at=now,
        updated_at=now,
    )
    if previous is not None and previous.checkpoint.get("discussion_revision"):
        run.checkpoint = {
            **run.checkpoint,
            "discussion_revision": deepcopy(previous.checkpoint["discussion_revision"]),
        }
    if previous is not None:
        from short_drama.agent.context.delegation_results import visible_business_outputs_locked
        from short_drama.service.assistant.execution.scope import root_scope_locked

        previous_scope = root_scope_locked(session, previous)
        run.checkpoint = {
            **run.checkpoint,
            "resume_history": True,
            "inherited_business_operation_ids": [
                str(item.id)
                for item in visible_business_outputs_locked(session, previous, previous_scope.id)
            ],
            **{
                key: deepcopy(value)
                for key, value in previous.checkpoint.items()
                if key.startswith("available_")
                or key in {"history_floor_turn_id", "memory_invalidated", "image_content_reads"}
            },
        }
    task.status, task.phase, task.error = "queued", "at_boundary", None
    session.add(run)
    session.flush()
    from short_drama.service.assistant.execution.scope import root_scope_locked

    root_scope_locked(session, run)
    task_event(session, conversation, task, "task.queued", run=run)
    return run


def close_unstarted_calls(session, run):
    calls = session.scalars(
        select(AgentToolCall)
        .where(AgentToolCall.run_id == run.id)
        .order_by(AgentToolCall.turn_id, AgentToolCall.call_index)
        .with_for_update()
    ).all()
    for call in calls:
        if call.status in {"prepared", "ready"}:
            call.status, call.updated_at = "cancelled", utcnow()
            call.result = {"skipped": "task_boundary_changed", "executed": False}


def pending_task_locked(session, task):
    """只预览已受理输入；检查点落盘之前不前移产品水位。"""
    projection = SimpleNamespace(
        **{key: deepcopy(value) for key, value in vars(task).items() if not key.startswith("_")}
    )
    for item in AgentTaskDAO(session).inputs(task):
        for field in (
            "context_snapshot",
            "execution_target",
            "selected_skills",
        ):
            if field in item.payload:
                setattr(projection, field, deepcopy(item.payload[field]))
        projection.applied_input_revision = item.input_revision
    return projection


def apply_inputs_locked(session, conversation, task, run, *, through_revision=None):
    if task.status in {"stopped", "paused_error", "paused_budget", "succeeded"}:
        return False
    dao = AgentTaskDAO(session)
    if dao.has_active_operations(task.id):
        return False
    inputs = dao.inputs(task)
    if through_revision is not None:
        inputs = [item for item in inputs if item.input_revision <= through_revision]
    if not inputs:
        return False
    if task.applied_input_revision:
        task.plan_revision += 1
        close_unstarted_calls(session, run)
    for item in inputs:
        payload = item.payload
        if "script_reference" in payload:
            task.effective_state = {
                **task.effective_state,
                "script_reference": payload["script_reference"],
            }
        if "context_snapshot" in payload:
            state = deepcopy(task.effective_state)
            state["legacy_subject_keys"] = sorted(
                set(state.get("legacy_subject_keys", []))
                | subject_memory_keys(task.context_snapshot)
            )
            task.effective_state = state
            task.context_snapshot = payload["context_snapshot"]
        if "selected_skills" in payload:
            task.selected_skills = payload["selected_skills"]
        if "execution_target" in payload:
            task.execution_target = payload["execution_target"]
        item.applied_plan_revision = task.plan_revision
    task.applied_input_revision = inputs[-1].input_revision
    task.row_version += 1
    task.updated_at = utcnow()
    checkpoint = deepcopy(run.checkpoint)
    checkpoint["task_input_revision"] = task.applied_input_revision
    checkpoint["task_plan_revision"] = task.plan_revision
    run.checkpoint = checkpoint
    task_event(
        session,
        conversation,
        task,
        "task.input.applied",
        run=run,
        applied_input_revision=str(task.applied_input_revision),
    )
    return True


def finish_task_attempt_locked(session, conversation, run, status, error):
    task = task_for_run(session, run)
    if task is None:
        return
    if AgentTaskDAO(session).has_active_operations(task.id):
        raise RuntimeError("Cannot finish an Agent attempt before its active operation settles")
    # Boundary controls set their disposition before closing the old attempt.
    if task.status not in {"stopped", "paused_error", "paused_budget"}:
        task.status = (
            "succeeded"
            if status == "succeeded"
            else "paused_budget"
            if (error or {}).get("code")
            in {
                "agent_budget_exhausted",
                "agent_tool_budget_exhausted",
                "agent_context_budget_exhausted",
                "assistant_media_budget_exhausted",
            }
            else "paused_error"
            if status == "failed"
            else "stopped"
        )
    task.phase = "terminal"
    task.error = error
    task.row_version += 1
    task.updated_at = utcnow()
    task_event(
        session,
        conversation,
        task,
        "task.paused" if task.status.startswith("paused_") else f"task.{task.status}",
        run=run,
        status=task.status,
    )
