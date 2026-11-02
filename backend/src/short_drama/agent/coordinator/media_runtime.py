"""Refresh durable wait readiness; business settlement belongs to operations Services."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from short_drama.agent.core.access import lock_run
from short_drama.agent.task.queries import task_for_run
from short_drama.core.time import utcnow
from short_drama.domain import Project
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.service.assistant.execution.waiting import collect_ready_waits
from short_drama.service.assistant.operations.media_settlement import settle_batch_locked

logger = logging.getLogger(__name__)


def collect_media_results(factory, settings, limit=100):
    # No model calls or busy polling; the existing scheduler revisits durable waits.
    with factory() as session:
        ids = session.scalars(
            select(AgentOperation.id)
            .where(
                AgentOperation.tool_name == "workbench__submit_generation_batch",
                AgentOperation.status.in_(("waiting_media", "reconciling")),
            )
            .order_by(AgentOperation.updated_at, AgentOperation.id)
            .limit(limit)
        ).all()
    collected = 0
    for identifier in ids:
        try:
            with factory.begin() as session:
                parent = session.execute(
                    select(AgentOperation.run_id, AgentTask.execution_target)
                    .join(
                        AgentTask,
                        AgentTask.id == AgentOperation.task_id,
                    )
                    .where(AgentOperation.id == identifier)
                ).one_or_none()
                if parent is None:
                    continue
                if parent.execution_target and parent.execution_target.get("project_id"):
                    session.scalar(
                        select(Project)
                        .where(Project.id == int(parent.execution_target["project_id"]))
                        .with_for_update()
                    )
                rows = lock_run(session, parent.run_id, skip_locked=True)
                if rows is None:
                    continue
                _project, conversation, run = rows
                operation = session.scalar(
                    select(AgentOperation)
                    .where(
                        AgentOperation.id == identifier,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if operation.status not in {"waiting_media", "reconciling"}:
                    continue
                collected += int(
                    settle_batch_locked(
                        session, conversation, task_for_run(session, run), run, operation, settings
                    )
                )
                operation.updated_at = utcnow()
        except SQLAlchemyError as error:
            logger.warning(
                "Assistant media settlement unavailable; operation_id=%s error=%s",
                identifier,
                type(error).__name__,
            )
    return collected + collect_ready_waits(factory, settings, limit)
