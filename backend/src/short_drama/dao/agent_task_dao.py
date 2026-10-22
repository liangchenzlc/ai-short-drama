"""Task reads share the caller's conversation lock and never commit."""

from sqlalchemy import case, select

from short_drama.domain.agent import AgentRun
from short_drama.domain.agent_task import (
    AgentOperation,
    AgentTask,
    AgentTaskControl,
    AgentTaskInput,
)

OPEN_OPERATIONS = ("running", "waiting_media", "reconciling")
RESUMABLE_TASKS = ("stopped", "paused_error", "paused_budget")
ACTIVE_TASKS = ("queued", "running", "waiting_media")


class AgentTaskDAO:
    def __init__(self, session):
        self.session = session

    def task(self, identifier, *, conversation_id=None, lock=False, refresh=True):
        query = select(AgentTask).where(AgentTask.id == identifier)
        if conversation_id is not None:
            query = query.where(AgentTask.conversation_id == conversation_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=refresh)
        return self.session.scalar(query)

    def selected_task(self, conversation_id):
        return self.session.scalar(
            select(AgentTask)
            .where(
                AgentTask.conversation_id == conversation_id,
                AgentTask.status.in_((*ACTIVE_TASKS, *RESUMABLE_TASKS)),
            )
            .order_by(
                case((AgentTask.status.in_(("running", "waiting_media")), 0), else_=1),
                AgentTask.id.desc(),
            )
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def latest_run(self, task_id):
        return self.session.scalar(
            select(AgentRun)
            .where(AgentRun.task_id == task_id)
            .order_by(AgentRun.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def active_operation(self, task_id, *, lock=True):
        query = select(AgentOperation).where(
            AgentOperation.task_id == task_id, AgentOperation.status.in_(OPEN_OPERATIONS)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def inputs(self, task):
        return self.session.scalars(
            select(AgentTaskInput)
            .where(
                AgentTaskInput.task_id == task.id,
                AgentTaskInput.input_revision > task.applied_input_revision,
                AgentTaskInput.input_revision <= task.input_revision,
            )
            .order_by(AgentTaskInput.input_revision)
            .with_for_update()
        ).all()

    def controls(self, task):
        return self.session.scalars(
            select(AgentTaskControl)
            .where(AgentTaskControl.task_id == task.id, AgentTaskControl.applied_at.is_(None))
            .order_by(AgentTaskControl.seq)
            .with_for_update()
        ).all()
