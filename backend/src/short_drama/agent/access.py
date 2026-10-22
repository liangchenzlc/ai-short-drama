"""Project-first Agent row locking, decision permission and model fencing."""

from sqlalchemy import select

from short_drama.agent.model_gateway import AgentGatewayError
from short_drama.ai.model_identity import model_credential_identity
from short_drama.domain import AIModelConfig, Project
from short_drama.domain.agent import AgentConversation, AgentRun
from short_drama.domain.collaboration import User


class LeaseLost(Exception):
    pass


def lock_run(session, run_id, *, skip_locked=False):
    """All mutations, including publisher/recovery, lock Project before private rows."""
    parent = session.execute(
        select(AgentRun.conversation_id, AgentConversation.project_id)
        .join(AgentConversation, AgentConversation.id == AgentRun.conversation_id)
        .where(AgentRun.id == run_id)
    ).first()
    if parent is None:
        return None
    project = (
        session.scalar(
            select(Project)
            .where(Project.id == parent.project_id)
            .with_for_update(skip_locked=skip_locked)
        )
        if parent.project_id is not None
        else None
    )
    if project is None and parent.project_id is not None:
        return None
    conversation = session.scalar(
        select(AgentConversation)
        .where(AgentConversation.id == parent.conversation_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    if conversation is None:
        return None
    task_id = session.scalar(select(AgentRun.task_id).where(AgentRun.id == run_id))
    if task_id is not None:
        from short_drama.dao.agent_task_dao import AgentTaskDAO

        if AgentTaskDAO(session).task(task_id, conversation_id=conversation.id, lock=True) is None:
            return None
    run = session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    return (project, conversation, run) if run is not None else None


def may_decide(session, project, conversation, run):
    # Locking reads see current committed identity/membership after waiting for
    # the Project lock; ordinary MySQL REPEATABLE READ could use an older view.
    user = session.scalar(select(User).where(User.id == run.initiated_by).with_for_update())
    if (
        user is None
        or user.status != "active"
        or not user.email_verified_at
        or (project is not None and project.archived_at)
        or conversation.status != "active"
        or conversation.owner_user_id != run.initiated_by
    ):
        return False
    if conversation.scope_version == 3:
        from short_drama.agent.task_queries import task_for_run

        task = task_for_run(session, run)
        return (
            (
                conversation.project_id is None
                and project is None
                or project is not None
                and project.id == conversation.project_id
                and project.owner_user_id == user.id
            )
            and task is not None
            and task.conversation_id == conversation.id
            and task.status in {"queued", "running", "waiting_media"}
            and (run.checkpoint or {}).get("purpose") == "global_assistant"
        )
    if project is None or project.owner_user_id != user.id:
        return False
    authorization = (run.checkpoint or {}).get("authorization")
    if conversation.scope_version == 2:
        from short_drama.agent.assistant_chat import validate_assistant_chat

        try:
            validate_assistant_chat(conversation, run)
        except AgentGatewayError:
            return False
        return True
    if (run.checkpoint or {}).get("purpose") == "assistant_chat":
        return False
    creation = session.scalar(select(Project).where(Project.id == conversation.project_id))
    return (
        creation is not None
        and creation.id == project.id
        and isinstance(authorization, dict)
        and authorization.get("mode") in {"auto", "discuss", "single", "workflow"}
    )


def model_unchanged(session, run):
    model = session.scalar(
        select(AIModelConfig).where(AIModelConfig.id == run.model_config_id).with_for_update()
    )
    snapshot = run.config_snapshot or {}
    return (
        model is not None
        and model.owner_user_id == run.initiated_by
        and model.enabled == 1
        and model.is_deleted == 0
        and model.service_type == "text"
        and model.row_version == snapshot.get("row_version")
        and model.model_key == snapshot.get("model_key")
        and model.base_url == snapshot.get("base_url")
        and model_credential_identity(model) == snapshot.get("credential_identity")
    )
