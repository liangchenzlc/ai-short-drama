"""Task boundaries are serialized with input/control receipt and operation claim."""

from short_drama.agent.task.state import close_unstarted_calls, task_event
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.service.base import utcnow


def apply_controls_locked(session, conversation, task, run):
    """No current operation loses permission. Caller holds conversation -> task -> run."""
    from short_drama.agent.core.state import finish_locked
    from short_drama.agent.runtime.events import finish_trace_locked

    dao = AgentTaskDAO(session)
    if dao.has_active_operations(task.id):
        if task.control_state == "stop_pending":
            # 立即撤销后续调用准入；当前事务/媒体业务仍按独立收据结算。
            run.cancel_requested = 1
        return False
    controls = dao.controls(task)
    if not controls:
        return False
    now = utcnow()
    for control in controls:
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
    close_unstarted_calls(session, run)
    task.status, task.phase = "stopped", "terminal"
    if run.status == "running" and run.lease_token is not None:
        # 图仍持租约；由 Worker 取消调用并等检查点写入退出后释放。
        run.cancel_requested = 1
        task_event(session, conversation, task, "task.stopping", run=run)
    else:
        finish_locked(session, conversation, run, "cancelled")
        finish_trace_locked(session, conversation, run)
        task_event(session, conversation, task, "task.stopped", run=run)
    if conversation.status == "deleting":
        from short_drama.agent.session.lifecycle import finalize_deletion_locked

        session.flush()
        finalize_deletion_locked(session, conversation)
    return True
