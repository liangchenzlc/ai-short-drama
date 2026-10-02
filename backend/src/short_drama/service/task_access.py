"""System-side check before a new paid submission. Poll/save preserve accepted work."""

from sqlalchemy import select

from short_drama.domain import Project
from short_drama.domain.collaboration import ProjectMember, User


def lock_resource_project(session, model, identifier):
    """Project precedes task/job locks, matching revocation and API transactions."""
    project_id = session.scalar(select(model.project_id).where(model.id == identifier))
    if project_id:
        session.scalar(select(Project).where(Project.id == project_id).with_for_update())


def may_submit(session, task):
    if task.initiated_by is None:
        # Unowned tasks never cross the paid submission boundary.
        return False
    user = session.get(User, task.initiated_by)
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
                select(ProjectMember.id).where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.user_id == user.id,
                    ProjectMember.status == "active",
                )
            )
            is not None
        )
    return task.scope_user_id == user.id
