"""Trusted sound-line scopes derived from selected snapshots and committed task effects."""

from copy import deepcopy
from typing import Any

from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.core.exceptions import Conflict
from short_drama.domain.agent_task import AgentOperation, AgentTask
from short_drama.service.assistant_dialogue_service import COMMON_VERSIONS, advance_sound_binding


def task_sound_sources(
    task: AgentTask, request: dict[str, Any], prior: list[AgentOperation]
) -> tuple[dict[str, dict[str, str]], set[str], set[str]]:
    project_id = str(task.execution_target["project_id"])
    current = {
        item["subject_key"]: item
        for item in snapshot_subjects(task.context_snapshot)
        if item["kind"] == "sound_line" and item["project_id"] == project_id
    }
    selected = {
        item["id"]
        for item in snapshot_subjects(request["snapshot"])
        if item["subject_key"] in current and item["project_id"] == project_id
    }
    changes = [
        change
        for operation in sorted(prior, key=lambda item: item.id)
        if (operation.result or {}).get("project_id") == project_id
        for change in operation.changes
    ]
    bindings = {
        item["id"]: deepcopy(item["sound_binding"])
        for item in current.values()
        if item["id"] in selected and item.get("sound_binding")
    }
    if selected - bindings.keys():
        raise Conflict("台词缺少可信声音版本，请主动刷新引用")
    created = set()
    for change in changes:
        if change["kind"] == "sound_line" and change["before"] is None:
            created.add(change["id"])
            # A newer user refresh is authoritative; an older creation must not lower it.
            if change["id"] not in bindings:
                bindings[change["id"]] = {
                    key: change["after"][key]
                    for key in (*COMMON_VERSIONS, "line_hash", "line_checksum")
                }
    return (
        {
            identifier: advance_sound_binding(binding, identifier, changes)
            for identifier, binding in bindings.items()
        },
        selected,
        created,
    )


def sound_write_scope(
    task: AgentTask, request: dict[str, Any], prior: list[AgentOperation]
) -> tuple[set[str], dict[str, str]]:
    bindings, selected, created = task_sound_sources(task, request, prior)
    directive = request["directive"]
    allowed = selected | created
    if directive.action == "create":
        binding = request["input"].payload.get("sound_write_binding")
        if not binding:
            raise Conflict("请先保存成片草稿，再发送创建台词指令")
        changes = [
            change
            for operation in prior
            if (operation.result or {}).get("project_id")
            == str(task.execution_target["project_id"])
            for change in operation.changes
        ]
        return allowed, advance_sound_binding(binding, "", changes)
    if directive.selector == "selected":
        allowed &= selected
    elif directive.selector == "results":
        allowed &= created
    else:
        allowed &= {directive.selector}
    if not allowed:
        raise Conflict("没有与原指令匹配的已选台词或本任务新建台词")
    common = [{key: bindings[identifier][key] for key in COMMON_VERSIONS} for identifier in allowed]
    if any(item != common[0] for item in common[1:]):
        raise Conflict("已选台词的声音或画面版本不一致，请核对并主动刷新引用")
    return allowed, common[0]
