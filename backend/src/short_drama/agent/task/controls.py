"""Task boundaries are serialized with input/control receipt and operation claim."""

from short_drama.agent.task.state import (
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
    from short_drama.agent.core.state import finish_locked

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
            # 记录跳过事件，不静默丢弃
            control.applied_at = now
            control.applied_after_operation_id = control.requested_after_operation_id
            task.applied_control_seq = max(task.applied_control_seq, control.seq)
            task_event(
                session,
                conversation,
                task,
                "control.skipped",
                run=run,
                control_id=str(control.id),
                control_seq=str(control.seq),
                kind=control.kind,
                reason="task_already_stopped",
            )
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
        if run.status == "running" and run.lease_token is not None:
            # A segment is in flight. Let the worker persist what it already streamed
            # and close the run; finishing here would discard that prefix.
            run.cancel_requested = 1
            task_event(session, conversation, task, "task.stopping", run=run)
        else:
            finish_locked(session, conversation, run, "cancelled")
            task_event(session, conversation, task, "task.stopped", run=run)
    elif resume is not None:
        from short_drama.agent.core.access import model_unchanged
        from short_drama.agent.core.events import TERMINAL

        if not model_unchanged(session, run):
            # 模型配置在任务暂停期间被修改：过期快照不能复制进新 run，否则必然准入失败。
            error = {"code": "agent_model_superseded"}
            if run.status in TERMINAL:
                task.status, task.phase, task.error = "paused_error", "terminal", error
                task.row_version += 1
                task.updated_at = now
                task_event(session, conversation, task, "task.paused", run=run, status=task.status)
            else:
                finish_locked(session, conversation, run, "failed", error)
            return True
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
        from short_drama.agent.session.lifecycle import finalize_deletion_locked

        session.flush()
        finalize_deletion_locked(session, conversation)
    return True
