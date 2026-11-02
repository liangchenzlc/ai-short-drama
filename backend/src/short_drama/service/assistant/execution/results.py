"""Freeze qualified resource identities for a new Run, without copying old tool state."""

import logging
from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.memory.sources import restricted_messages
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.assistant.context.context import AssistantContextDAO
from short_drama.dao.assistant.execution.scope import AgentExecutionScopeDAO
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTaskInput
from short_drama.schemas.assistant.tasks.task import TaskSubjectReference
from short_drama.service.assistant.context.subject import freeze_subject

logger = logging.getLogger(__name__)


def carry_results_locked(session, conversation, task, previous, payload):
    if "context_snapshot" in payload:
        return
    target = payload.get("execution_target", task.execution_target) or {}
    root = AgentExecutionScopeDAO(
        session, owner_id=conversation.owner_user_id, run_id=previous.id
    ).get(scope_key="root")
    if root is None:
        return
    restricted, _ = restricted_messages(session, conversation.id)
    subjects = {
        item["subject_key"]: deepcopy(item)
        for item in snapshot_subjects(task.context_snapshot)
        if int(item.get("carried_source_message_id", "0")) not in restricted
    }
    operations = session.scalars(
        select(AgentOperation)
        .where(
            AgentOperation.run_id == previous.id,
            AgentOperation.scope_id == root.id,
            AgentOperation.status == "succeeded",
        )
        .order_by(AgentOperation.id)
    ).all()
    dao = AssistantContextDAO(session)
    for operation in operations:
        project = (operation.result or {}).get("project_id")
        if project != target.get("project_id"):
            continue
        source = session.scalar(
            select(AgentTaskInput.message_id).where(
                AgentTaskInput.task_id == task.id,
                AgentTaskInput.input_revision == operation.input_revision,
                AgentTaskInput.applied_plan_revision.is_not(None),
            )
        )
        if source is None or source in restricted:
            continue
        for change in operation.changes:
            if (
                change["before"] is not None
                or change["after"] is None
                or change["kind"] not in {"asset", "shot", "node", "sound_line"}
            ):
                continue
            if len(subjects) >= 16:
                break
            reference = TaskSubjectReference(
                project_id=project,
                source_kind="canvas" if change["kind"] == "node" else "project",
                source_id=(operation.result or {}).get("canvas_id", project),
                kind=change["kind"],
                id=change["id"],
                revision=change["after"]["row_version"],
            )
            try:
                frozen = freeze_subject(dao, conversation.owner_user_id, reference)
            except (NotFound, WorkflowError) as error:
                if isinstance(error, WorkflowError) and error.code != "assistant_context_changed":
                    raise
                logger.info(
                    "agent_prior_result_unavailable",
                    extra={"operation_id": operation.id, "resource_id": change["id"]},
                )
                continue
            frozen["carried_source_message_id"] = str(source)
            subjects[frozen["subject_key"]] = frozen
    if subjects:
        payload["context_snapshot"] = {"schema_version": 2, "subjects": list(subjects.values())}
