"""Existing frozen scope and source rules, kept with their actual responsibility."""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.tools.contracts import CANVAS_TOOL as CANVAS_TOOL
from short_drama.core.exceptions import Conflict
from short_drama.domain import Project, ProjectCanvas
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.domain.canvas.document.canvas import CanvasNode


def _successful_operations(session: Session, task: AgentTask) -> list[AgentOperation]:
    return list(
        session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == task.id,
                or_(
                    and_(
                        AgentOperation.tool_name == CANVAS_TOOL,
                        AgentOperation.status == "succeeded",
                    ),
                    and_(
                        AgentOperation.tool_name == "submit_generation_batch",
                        AgentOperation.status.in_({"succeeded", "failed", "conflict"}),
                    ),
                ),
            )
            .order_by(AgentOperation.id)
        )
    )


def _scope(
    task: AgentTask, request: dict, prior: list[AgentOperation]
) -> dict[str, dict[str, str]]:
    target = task.execution_target

    def matches(item):
        return (
            item["source_kind"] == "canvas"
            and item["kind"] == "node"
            and item["project_id"] == target["project_id"]
            and item["source_id"] == target["canvas_id"]
        )

    current = {
        item["id"]: item for item in snapshot_subjects(task.context_snapshot) if matches(item)
    }
    selected = {
        item["id"]
        for item in snapshot_subjects(request["snapshot"])
        if matches(item) and item["id"] in current
    }
    versions = {
        key: item["write_versions"] for key, item in current.items() if item.get("write_versions")
    }
    names = {key: item["name"] for key, item in current.items()}
    created, deleted = set(), set()
    for operation in prior:
        result = operation.result or {}
        if (result.get("project_id"), result.get("canvas_id")) != (
            target["project_id"],
            target["canvas_id"],
        ):
            continue
        for change in operation.changes:
            if change["kind"] != "canvas_node":
                continue
            key, after = change["id"], change["after"]
            if change["before"] is None:
                created.add(key)
            if key in current or key in created:
                version = {field: after[field] for field in ("row_version", "private_revision")}
                existing = versions.get(key)
                if existing is None or all(
                    int(version[field]) >= int(existing[field]) for field in version
                ):
                    versions[key] = version
                names.setdefault(key, after["document"].get("title", ""))
            if after["deleted"]:
                deleted.add(key)
    directive = request["directive"]
    allowed = (selected | created) - deleted
    if directive.action != "create":
        if directive.selector == "selected":
            allowed &= selected
        elif directive.selector == "results":
            allowed &= created
        else:
            allowed = {
                key
                for key in allowed
                if key == directive.selector.removeprefix("ID").strip()
                or names.get(key) == directive.selector
            }
            if len(allowed) != 1:
                raise Conflict("指令节点不唯一或未选入，请明确节点名称或 ID")
        if not allowed:
            raise Conflict("没有与原指令匹配的已选节点或本任务成果")
    if allowed - versions.keys():
        raise Conflict("旧节点引用没有可信私人版本，请核对后主动刷新引用")
    return {key: versions[key] for key in sorted(allowed)}


def canvas_result_keys(session: Session, owner: int, task: AgentTask) -> set[str]:
    created = {
        (
            str((operation.result or {})["project_id"]),
            (operation.result or {})["canvas_id"],
            change["id"],
        )
        for operation in _successful_operations(session, task)
        for change in operation.changes
        if change["kind"] == "canvas_node" and change["before"] is None
    }
    if not created:
        return set()
    rows = session.execute(
        select(CanvasNode.node_key, ProjectCanvas.source_key, Project.id)
        .join(ProjectCanvas, ProjectCanvas.id == CanvasNode.canvas_id)
        .join(Project, Project.id == ProjectCanvas.project_id)
        .where(
            CanvasNode.node_key.in_({key for _project, _canvas, key in created}),
            CanvasNode.archived_at.is_(None),
            ProjectCanvas.archived_at.is_(None),
            Project.archived_at.is_(None),
            Project.workspace_mode == "infinite_canvas",
            Project.owner_user_id == owner,
        )
    ).all()
    return {
        f"{project}:{canvas}:node:{key}"
        for key, canvas, project in rows
        if (str(project), canvas, key) in created
    }
