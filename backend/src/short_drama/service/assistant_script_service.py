"""Explicit adoption of a frozen assistant script proposal; replay returns its receipt."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.task.state import task_event
from short_drama.agent.tools.script import bound_document
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain.agent_task import AgentOperation
from short_drama.schemas.assistant_script import ScriptProposalRead
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.project_writing import WritingVersion
from short_drama.service.project_writing_service import ProjectWritingService


def _load(service, operation_id):
    identifier = parse_identifier(operation_id)
    operation = service.session.get(AgentOperation, identifier)
    if operation is None:
        raise NotFound("剧本建议不存在")
    conversation, task = service._task(operation.task_id)
    operation = service.session.scalar(
        select(AgentOperation)
        .where(AgentOperation.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        operation.tool_name != "propose_script_patch"
        or operation.status != "succeeded"
        or not operation.changes
    ):
        raise NotFound("此操作没有可审阅的剧本建议")
    return conversation, task, operation, operation.changes[0]


def preview(service, operation_id):
    with service._transaction(read_only=True):
        _conversation, _task, operation, change = _load(service, operation_id)
        return ScriptProposalRead.model_validate(
            {
                **{
                    key: value
                    for key, value in change.items()
                    if key in ScriptProposalRead.model_fields
                },
                "before": change["before"]["content"],
                "after": change["after"]["content"],
                "operation_id": operation.id,
                "applied_version": (operation.result or {}).get("script_applied_version"),
            }
        ).model_dump(mode="json")


def apply(service, operation_id, payload):
    values = WritingVersion.model_validate(service._payload(WritingVersion, payload))
    with service._transaction():
        conversation, task, operation, change = _load(service, operation_id)
        applied = (operation.result or {}).get("script_applied_version")
        if applied:
            return {"project_id": change["project_id"], "content_version": applied}
        if values.content_version != int(change["content_version"]):
            raise WorkflowError(
                "assistant_script_changed", "建议的剧本版本不匹配，请重新生成建议", 409
            )
        project, script = bound_document(
            service.session,
            task.execution_target,
            {
                "project_id": change["project_id"],
                "script_id": change["script_id"],
                "content_version": change["content_version"],
            },
        )
        writing = ProjectWritingService(service.session)
        writing.dao.require_idle(project.id, "script")
        content = script.content if script else ""
        if content[change["start"] : change["end"]] != change["before"]["content"]:
            raise WorkflowError("assistant_script_changed", "原文已变化，建议未覆盖当前正文", 409)
        writing.write_script(
            project,
            script,
            content[: change["start"]] + change["after"]["content"] + content[change["end"] :],
            source="assistant",
        )
        writing._changed(project)
        operation.result = {
            **deepcopy(operation.result or {}),
            "script_applied_version": str(project.content_version),
        }
        operation.result_refs = [
            {**ref, "status": "applied", "content_version": str(project.content_version)}
            for ref in operation.result_refs
        ]
        operation.row_version += 1
        task_event(
            service.session,
            conversation,
            task,
            "artifact.script.applied",
            project_id=str(project.id),
            operation_id=str(operation.id),
        )
        return {"project_id": str(project.id), "content_version": str(project.content_version)}
