"""Read versioned current text and prepare an explicitly reviewed script change."""

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.project_writing_dao import ProjectWritingDAO
from short_drama.schemas.project_writing import writing_text


def bound_document(session, target, reference):
    if not target or target.get("mode") != "standard" or not reference:
        raise WorkflowError("assistant_script_unbound", "请先引用当前剧本再进行剧本创作", 422)
    if str(target["project_id"]) != str(reference["project_id"]):
        raise WorkflowError("assistant_script_scope", "剧本引用不属于当前任务目标", 422)
    dao = ProjectWritingDAO(session)
    project = dao.scoped_project(int(reference["project_id"]))
    if project.content_version != int(reference["content_version"]) or (
        str(project.editing_script_id) if project.editing_script_id else None
    ) != (str(reference["script_id"]) if reference["script_id"] else None):
        raise WorkflowError("assistant_script_changed", "剧本版本已变化，请重新引用当前剧本", 409)
    script = (
        dao.script(project.id, project.editing_script_id) if project.editing_script_id else None
    )
    content = script.content if script else ""
    if reference.get("end") is not None and reference["end"] > len(content):
        raise WorkflowError("assistant_script_changed", "剧本选区已变化，请重新选择", 409)
    return project, script


def read_locked(session, conversation, task, operation, values, settings):
    reference = task.effective_state.get("script_reference")
    project, script = bound_document(session, task.execution_target, reference)
    content = script.content if script else ""
    start, end = reference.get("start") or 0, reference.get("end")
    end = len(content) if end is None else end
    offset = min(start + values.offset, end)
    following = min(offset + values.limit, end)
    return {
        "project_id": str(project.id),
        "script_id": str(script.id) if script else None,
        "content_version": str(project.content_version),
        "content": content[offset:following],
        "start": offset,
        "end": following,
        "authorized_start": start,
        "authorized_end": end,
        "next_offset": following - start if following < end else None,
    }


def propose_locked(session, conversation, task, operation, values, settings):
    reference = task.effective_state.get("script_reference")
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
