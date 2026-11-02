from short_drama.agent.context.delegation import delegated_inputs_locked
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.creation.writing.script.documents import writing_text
from short_drama.service.assistant.operations.script_read import bound_document


def propose_locked(session, conversation, task, operation, values, settings):
    inputs = delegated_inputs_locked(session, operation.run_id, operation.scope_id)
    reference = (
        inputs.get("script_reference")
        if inputs is not None
        else task.effective_state.get("script_reference")
    )
    project, script = bound_document(session, task.execution_target, reference)
    content = script.content if script else ""
    allowed_start = reference.get("start") or 0
    allowed_end = reference.get("end")
    allowed_end = len(content) if allowed_end is None else allowed_end
    if (
        values.content_version != project.content_version
        or values.script_id != project.editing_script_id
    ):
        raise WorkflowError("assistant_script_changed", "请使用刚读取的剧本身份和版本", 409)
    if not allowed_start <= values.start <= values.end <= allowed_end:
        raise WorkflowError("assistant_script_range", "修改范围超出本次引用的正文或选区", 422)
    try:
        writing_text(content[: values.start] + values.replacement + content[values.end :])
    except ValueError as exc:
        raise WorkflowError(
            "assistant_script_too_large", "修改后的剧本不能超过 1 MiB", 422
        ) from exc
    operation.changes = [
        {
            "id": str(operation.id),
            "kind": "script_proposal",
            "project_id": str(project.id),
            "script_id": str(script.id) if script else None,
            "content_version": str(project.content_version),
            "start": values.start,
            "end": values.end,
            "before": {"content": content[values.start : values.end]},
            "after": {"kind": "script_proposal", "content": values.replacement},
        }
    ]
    operation.result_refs = [
        {
            "kind": "script_proposal",
            "id": str(operation.id),
            "project_id": str(project.id),
            "name": "剧本修改建议",
            "status": "pending_review",
        }
    ]
    return {
        "operation_id": str(operation.id),
        "status": "pending_review",
        "message": "修改建议已准备，等待用户审阅后写入正文；当前正文尚未改变。",
    }
