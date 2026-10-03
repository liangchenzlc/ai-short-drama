"""System-side check before a new paid submission. Poll/save preserve accepted work."""

from sqlalchemy import select

from short_drama.domain import Project
from short_drama.domain.collaboration import ProjectMember, User


def lock_resource_project(session, model, identifier):
    """Project precedes task/job locks, matching revocation and API transactions."""
    project_id = session.scalar(select(model.project_id).where(model.id == identifier))
    if project_id:
        session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if model.__tablename__ == "async_tasks":
        from short_drama.domain import AIGenerationRecord

        snapshot = (
            session.scalar(
                select(AIGenerationRecord.config_snapshot)
                .where(AIGenerationRecord.task_id == identifier)
                .order_by(AIGenerationRecord.call_no)
                .limit(1)
            )
            or {}
        )
        if snapshot.get("agent_managed") is True:
            # Private linkage is never copied into project-shared generation records.
            from short_drama.agent.runtime import lock_run
            from short_drama.domain.agent import AgentToolCall

            run_id = session.scalar(
                select(AgentToolCall.run_id).where(AgentToolCall.generation_task_id == identifier)
            )
            rows = lock_run(session, run_id) if run_id is not None else None
            if rows is not None:
                session.scalar(
                    select(AgentToolCall)
                    .where(AgentToolCall.generation_task_id == identifier)
                    .with_for_update()
                )
            session.info.setdefault("agent_task_parents", {})[identifier] = rows


def may_submit(session, task, *, agent_enabled=None):
    parents = session.info.get("agent_task_parents", {})
    if task.id in parents:
        from short_drama.agent.runtime import may_decide
        from short_drama.agent.state import TERMINAL

        rows = parents[task.id]
        if rows is None or agent_enabled is False:
            return False
        project, conversation, run = rows
        if (
            run.status in TERMINAL
            or run.cancel_requested
            or not may_decide(session, project, conversation, run)
        ):
            return False
    if task.initiated_by is None:
        # Unowned tasks never cross the paid submission boundary.
        return False
    user = session.scalar(
        select(User)
        .where(User.id == task.initiated_by)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.status != "active" or not user.email_verified_at:
        return False
    if task.project_id:
        project = session.scalar(
            select(Project).where(Project.id == task.project_id).with_for_update()
        )
        if project is None or project.archived_at:
            return False
        if project.owner_user_id == user.id:
            return True
        return (
            session.scalar(
                select(ProjectMember.id)
                .where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.user_id == user.id,
                    ProjectMember.status == "active",
                )
                .with_for_update()
            )
            is not None
        )
    return task.scope_user_id == user.id
