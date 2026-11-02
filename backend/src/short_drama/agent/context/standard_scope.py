"""Existing frozen scope and source rules, kept with their actual responsibility."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.agent.tools.contracts import STANDARD_TOOL as STANDARD_TOOL
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.domain import Asset, Project, ShotScript
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.service.assistant.operations.dialogue import sound_line_state


def _successful_operations(session: Session, task: AgentTask) -> list[AgentOperation]:
    return list(
        session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name == STANDARD_TOOL,
                AgentOperation.status == "succeeded",
            )
            .order_by(AgentOperation.id)
        ).all()
    )


def _primary_operations(session: Session, task: AgentTask) -> list[AgentOperation]:
    return list(
        session.scalars(
            select(AgentOperation)
            .where(
                AgentOperation.task_id == task.id,
                AgentOperation.tool_name.in_(
                    ("set_image_group_primary", "submit_generation_batch")
                ),
                AgentOperation.status.in_(("succeeded", "failed", "conflict")),
            )
            .order_by(AgentOperation.id)
        ).all()
    )


def standard_result_keys(session: Session, owner: int, task: AgentTask) -> set[str]:
    created = {
        (change["kind"], int(change["id"]))
        for operation in _successful_operations(session, task)
        for change in operation.changes
        if change["before"] is None and change["kind"] in {"asset", "shot"}
    }
    result = set()
    for kind, model in (("asset", Asset), ("shot", ShotScript)):
        identifiers = [identifier for item_kind, identifier in created if item_kind == kind]
        if not identifiers:
            continue
        rows = session.execute(
            select(model.id, model.project_id)
            .join(Project, Project.id == model.project_id)
            .where(
                model.id.in_(identifiers),
                model.deleted_at.is_(None),
                Project.owner_user_id == owner,
                Project.workspace_mode == "standard",
                Project.archived_at.is_(None),
            )
        ).all()
        result.update(f"{project}:{project}:{kind}:{identifier}" for identifier, project in rows)
    for operation in _successful_operations(session, task):
        project_id = (operation.result or {}).get("project_id")
        if (
            project_id is None
            or session.scalar(
                select(Project.id).where(
                    Project.id == int(project_id),
                    Project.owner_user_id == owner,
                    Project.workspace_mode == "standard",
                    Project.archived_at.is_(None),
                )
            )
            is None
        ):
            continue
        for change in operation.changes:
            if change["kind"] != "sound_line" or change["before"] is not None:
                continue
            try:
                sound_line_state(session, project_id, change["id"])
            except NotFound:
                continue
            result.add(f"{project_id}:{project_id}:sound_line:{change['id']}")
    return result


def _scope(task: AgentTask, request: dict, prior: list[AgentOperation]) -> tuple[dict, dict]:
    project_id = str(task.execution_target["project_id"])
    current = {
        (item["kind"], item["id"]): item
        for item in snapshot_subjects(task.context_snapshot)
        if item["source_kind"] == "project"
        and item["project_id"] == project_id
        and item["kind"] in {"asset", "shot"}
    }
    selected = {
        (item["kind"], item["id"])
        for item in snapshot_subjects(request["snapshot"])
        if (item["kind"], item["id"]) in current and item["project_id"] == project_id
    }
    created, versions, names = set(), {}, {}
    for key, item in current.items():
        versions[key], names[key] = int(item["revision"]), item["name"]
    for operation in prior:
        if (operation.result or {}).get("project_id") != project_id:
            continue
        for change in operation.changes:
            if change["kind"] not in {"asset", "shot"}:
                continue
            key = change["kind"], change["id"]
            if change["before"] is None:
                created.add(key)
            if key in current or key in created:
                versions[key] = max(versions.get(key, 0), int(change["after"]["row_version"]))
                if key not in names:
                    names[key] = change["after"].get("name", change["after"].get("title", ""))
    directive = request["directive"]
    allowed = selected | created
    if directive.action != "create":
        targets = {key for key in allowed if key[0] == directive.kind}
        if directive.selector == "selected":
            targets &= selected
        elif directive.selector == "results":
            targets &= created
        else:
            targets = {
                key
                for key in targets
                if key[1] == directive.selector.removeprefix("ID").strip()
                or names.get(key) == directive.selector
            }
            if len(targets) != 1:
                raise Conflict("指令对象不唯一或未选入，请明确素材／分镜的名称或 ID")
        if not targets:
            raise Conflict("没有与原指令匹配的已选主体或本任务成果")
        allowed = targets | (
            {key for key in allowed if key[0] == "asset"} if directive.associate else set()
        )
    return {
        kind: [key[1] for key in allowed if key[0] == kind] for kind in ("asset", "shot")
    }, versions
