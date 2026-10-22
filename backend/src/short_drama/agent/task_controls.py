"""Task boundaries are serialized with input/control receipt and operation claim."""

from short_drama.agent.task_state import (
    apply_inputs_locked,
    close_unstarted_calls,
    new_attempt_locked,
    task_event,
)
from short_drama.dao.agent_task_dao import AgentTaskDAO
from short_drama.domain.agent import AgentMessage
from short_drama.service.base import utcnow


def apply_controls_locked(session, conversation, task, run):
    """No current operation loses permission. Caller holds conversation -> task -> run."""
    from short_drama.agent.state import finish_locked

    dao = AgentTaskDAO(session)
    if dao.active_operation(task.id) is not None:
        return False
    controls = dao.controls(task)
    if not controls:
        return False
    stopped = task.status in {"stopped", "paused_error", "paused_budget"}
    resume = None
    for control in controls:
        if control.kind in {"stop", "delete"}:
            stopped = True
            resume = None
        elif control.kind == "resume":
            stopped = False
            resume = control
    now = utcnow()
    for control in controls:
        if stopped and control.kind == "redirect":
            continue
        control.applied_at = now
        control.applied_after_operation_id = control.requested_after_operation_id
        task.applied_control_seq = max(task.applied_control_seq, control.seq)
        task_event(
            session,
            conversation,
            task,
            "control.applied",
            run=run,
            control_id=str(control.id),
            control_seq=str(control.seq),
            kind=control.kind,
        )
    task.control_state = "none"
    task.row_version += 1
    task.updated_at = now
    if stopped:
        close_unstarted_calls(session, run)
        task.status, task.phase = "stopped", "terminal"
        finish_locked(session, conversation, run, "cancelled")
        task_event(session, conversation, task, "task.stopped", run=run)
    elif resume is not None:
        close_unstarted_calls(session, run)
        finish_locked(session, conversation, run, "cancelled")
        message = session.get(AgentMessage, resume.source_message_id)
        next_run = new_attempt_locked(
            session, conversation, task, message, run.model_config_id, run.config_snapshot, run
        )
        task_event(session, conversation, task, "task.resumed", run=next_run)
    else:
        apply_inputs_locked(session, conversation, task, run)
        task_event(session, conversation, task, "plan.redirected", run=run)
    if conversation.status == "deleting":
        from short_drama.agent.session_lifecycle import finalize_deletion_locked

        session.flush()
        finalize_deletion_locked(session, conversation)
    return True
