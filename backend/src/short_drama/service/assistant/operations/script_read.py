"""Read versioned current text and prepare an explicitly reviewed script change."""

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.creation.writing.script.documents import ProjectWritingDAO


def bound_document(session, target, reference, *, for_update=True):
    if not target or target.get("mode") != "standard" or not reference:
        raise WorkflowError("assistant_script_unbound", "请先引用当前剧本再进行剧本创作", 422)
    if str(target["project_id"]) != str(reference["project_id"]):
        raise WorkflowError("assistant_script_scope", "剧本引用不属于当前任务目标", 422)
    dao = ProjectWritingDAO(session)
    project = dao.scoped_project(int(reference["project_id"]), for_update=for_update)
    if project.content_version != int(reference["content_version"]) or (
        str(project.editing_script_id) if project.editing_script_id else None
    ) != (str(reference["script_id"]) if reference["script_id"] else None):
        raise WorkflowError("assistant_script_changed", "剧本版本已变化，请重新引用当前剧本", 409)
    script = (
        dao.script(project.id, project.editing_script_id, for_update=for_update)
        if project.editing_script_id
        else None
    )
    content = script.content if script else ""
    if reference.get("end") is not None and reference["end"] > len(content):
        raise WorkflowError("assistant_script_changed", "剧本选区已变化，请重新选择", 409)
    return project, script


def read_current_script_locked(session, identity, values, receipt_id, settings):
    if identity.scope.subject_policy == "selected":
        reference = identity.scope.script_reference
        if reference is None or any(
            str(reference[key]) != str(getattr(values, key))
            for key in ("project_id", "script_id", "content_version")
        ):
            raise WorkflowError("assistant_script_scope", "剧本未包含在本次授权引用中", 403)
        if (values.start or 0) < (reference.get("start") or 0) or (
            reference.get("end") is not None
            and (values.end is None or values.end > reference["end"])
        ):
            raise WorkflowError("assistant_script_scope", "读取不能超出已引用剧本选区", 403)
    project, script = bound_document(
        session,
        {"project_id": str(values.project_id), "mode": "standard"},
        values.model_dump(mode="json"),
    )
    content = script.content if script else ""
    start = values.start or 0
    end = len(content) if values.end is None else values.end
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
