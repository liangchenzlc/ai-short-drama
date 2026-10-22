"""Task boundaries are serialized with input/control receipt and operation claim."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.run_events import append_event, budget_limits, initial_usage
from short_drama.agent.subject_context import subject_catalog, subject_memory_keys
from short_drama.agent.task_queries import task_for_run as task_for_run
from short_drama.dao.agent_task_dao import AgentTaskDAO
from short_drama.domain.agent import AgentRun, AgentToolCall
from short_drama.domain.agent_undo import AgentBatchUndo
from short_drama.schemas.assistant_task import OperationRead, TaskRead
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id


def read_operation(row, *, undo_receipts=()):
    return OperationRead(
        id=row.id,
        task_id=row.task_id,
        run_id=row.run_id,
        call_id=row.tool_call_id,
        tool_name=row.tool_name,
        tool_version=row.tool_version,
        plan_revision=row.plan_revision,
        state_version=row.row_version,
        phase=row.phase,
        outcome=row.status,
        result_refs=row.result_refs,
        skill_activations=(row.result or {}).get("skill_activations", []),
        undo_receipts=list(undo_receipts),
        error=row.error,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def read_operations(session, rows):
    from short_drama.schemas.assistant_undo import BatchUndoRead

    receipts = {}
    if rows:
        for row in session.scalars(
            select(AgentBatchUndo)
            .where(AgentBatchUndo.operation_id.in_([row.id for row in rows]))
            .order_by(AgentBatchUndo.id)
        ):
            receipts.setdefault(row.operation_id, []).append(
                BatchUndoRead.model_validate(row, from_attributes=True).model_dump(mode="json")
            )
    return [read_operation(row, undo_receipts=receipts.get(row.id, ())) for row in rows]


def read_task(session, task):
    from short_drama.agent.skill_contracts import activation_summary, activations

    operation = AgentTaskDAO(session).active_operation(task.id, lock=False)
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
        execution_target=task.execution_target,
        memory_project_ids=task.memory_project_ids,
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
        skill_activations=[activation_summary(item) for item in activations(task)],
        media_limits=task.media_limits,
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
    session, conversation, task, message, model_id, snapshot, previous=None, *, trigger_revision=0
):
    now = utcnow()
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
            "history": None,
            "pending_results": None,
            "task_input_revision": task.applied_input_revision,
            "task_plan_revision": task.plan_revision,
            "authorization": {"mode": "discuss", "steps": []},
            "user_prompt": task.goal,
        },
        budget=budget_limits("global_assistant"),
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
    task.status, task.phase, task.error = "queued", "at_boundary", None
    session.add(run)
    session.flush()
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
    if calls:
        latest = max(call.turn_id for call in calls)
        checkpoint = deepcopy(run.checkpoint)
        checkpoint["pending_results"] = {
            "calls": {
                call.provider_call_id: call.result or {"error": (call.error or {}).get("code")}
                for call in calls
                if call.turn_id == latest
            }
        }
        run.checkpoint = checkpoint


def apply_inputs_locked(session, conversation, task, run):
    if task.status in {"stopped", "paused_error", "paused_budget", "succeeded"}:
        return False
    dao = AgentTaskDAO(session)
    if dao.active_operation(task.id) is not None:
        return False
    inputs = dao.inputs(task)
    if not inputs:
        return False
    if task.applied_input_revision:
        task.plan_revision += 1
        close_unstarted_calls(session, run)
    for item in inputs:
        payload = item.payload
        if "context_snapshot" in payload:
            state = deepcopy(task.effective_state)
            state["legacy_subject_keys"] = sorted(
                set(state.get("legacy_subject_keys", []))
                | subject_memory_keys(task.context_snapshot)
            )
            task.effective_state = state
            task.context_snapshot = payload["context_snapshot"]
        if "selected_skills" in payload:
            from short_drama.agent.skill_runtime import apply_manual_skills

            task.selected_skills = payload["selected_skills"]
            apply_manual_skills(task, task.selected_skills, item.message_id)
        if "execution_target" in payload:
            task.execution_target = payload["execution_target"]
        if "memory_project_ids" in payload:
            task.memory_project_ids = payload["memory_project_ids"]
        if "media_limits" in payload:
            task.media_limits = payload["media_limits"]
        item.applied_plan_revision = task.plan_revision
    task.applied_input_revision = inputs[-1].input_revision
    task.row_version += 1
    task.updated_at = utcnow()
    checkpoint = deepcopy(run.checkpoint)
    checkpoint["task_input_revision"] = task.applied_input_revision
    checkpoint["task_plan_revision"] = task.plan_revision
    if any(
        {"context_snapshot", "memory_project_ids", "selected_skills"}.intersection(item.payload)
        or "retained_attachment_ids" in item.payload
        or item.payload.get("attachments")
        for item in inputs
    ):
        checkpoint["history"] = None
        checkpoint["pending_results"] = None
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
    if AgentTaskDAO(session).active_operation(task.id) is not None:
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
