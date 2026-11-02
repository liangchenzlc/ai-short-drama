"""What the user is allowed to see for each assistant tool.

The frontend must not keep its own tool-name allowlist: it drifts from the executor
and leaks internal steps. Visibility and the user-facing label are decided here and
travel with every step event and snapshot.

``silent``   collapsed internal reads; still available in execution trace.
``step``     one line in a collapsed group, no arguments or payloads.
``artifact`` a produced work item; always rendered on its own.
"""

from short_drama.agent.runtime.trace_content import display_safe, utf8_preview

SILENT = "silent"
STEP = "step"
ARTIFACT = "artifact"
VISIBILITIES = frozenset({SILENT, STEP, ARTIFACT})

_VISIBILITY = {
    "workbench__read_current_script": STEP,
    "runtime__propose_script_patch": ARTIFACT,
    "runtime__search_skills": SILENT,
    "runtime__load_skill": STEP,
    "runtime__read_skill_resource": STEP,
    "runtime__read_operation_receipt": SILENT,
    "runtime__get_generation_policy": SILENT,
    "search_memories": SILENT,
    "read_memories": SILENT,
    "runtime__memory_catalog": SILENT,
    "runtime__search_memory": SILENT,
    "runtime__read_memory": SILENT,
    "runtime__read_conversation_history": SILENT,
    "runtime__remember_memory": SILENT,
    "runtime__update_memory": SILENT,
    "runtime__forget_memory": SILENT,
    "runtime__restore_memory": SILENT,
    "runtime__describe_creation_target": STEP,
    "runtime__read_selected_subjects": STEP,
    "runtime__read_task_results": STEP,
    "workbench__read_image_group": STEP,
    "workbench__read_image_content": STEP,
    "workbench__get_generation_status": STEP,
    "workbench__apply_standard_patch": ARTIFACT,
    "workbench__apply_canvas_patch": ARTIFACT,
    "workbench__submit_generation_batch": ARTIFACT,
    "workbench__set_image_group_primary": ARTIFACT,
}

_LABELS = {
    "workbench__read_current_script": "读取当前剧本",
    "runtime__propose_script_patch": "准备剧本修改建议",
    "runtime__load_skill": "读取创作方法",
    "runtime__read_skill_resource": "读取创作方法",
    "runtime__describe_creation_target": "核对写入目标",
    "runtime__read_selected_subjects": "读取选定内容",
    "runtime__read_task_results": "读取作品内容",
    "workbench__read_image_group": "读取图片组",
    "workbench__read_image_content": "读取图片",
    "workbench__get_generation_status": "查询生成进度",
    "workbench__apply_standard_patch": "保存到正文",
    "workbench__apply_canvas_patch": "更新画布",
    "workbench__submit_generation_batch": "生成媒体",
    "workbench__set_image_group_primary": "设置主图",
}

_FALLBACK_LABEL = "执行创作操作"


def visibility(tool_name: str) -> str:
    """Unknown tools stay visible as a generic step rather than disappearing."""
    return _VISIBILITY.get(tool_name, STEP)


def label(tool_name: str) -> str:
    return _LABELS.get(tool_name, _FALLBACK_LABEL)


def is_silent(tool_name: str) -> bool:
    return visibility(tool_name) == SILENT


def _default_target(*, spec, session, task, **_facts):
    from short_drama.domain.projects.management.project import Project
    from short_drama.schemas.assistant.operations.trace import ExecutionTarget

    target = ExecutionTarget(
        kind="task", id=str(task.id), display_name=utf8_preview(task.goal, 240)
    )
    if task.execution_target:
        project = session.get(Project, int(task.execution_target["project_id"]))
        if project:
            target = ExecutionTarget(
                kind="project",
                id=str(project.id),
                display_name=project.name,
                version=project.row_version,
            )
    return label(spec.name), target, []


def _skill_target(skill, arguments, session):
    from short_drama.domain.skills.catalog.skill import AgentSkillVersion
    from short_drama.schemas.assistant.operations.trace import ExecutionTarget

    target = ExecutionTarget(
        kind="skill",
        id=skill["id"],
        display_name=skill["name"],
        version=skill["content_version"],
        path=arguments.get("path"),
        offset=arguments.get("offset"),
        length=arguments.get("limit"),
    )
    bindings = []
    version = (
        session.get(AgentSkillVersion, int(skill["version_id"]))
        if skill.get("version_id")
        else None
    )
    if (
        version
        and version.skill_key == skill["id"]
        and version.checksum_sha256 == skill.get("package_checksum")
    ):
        resource = next((item for item in version.resources if item["path"] == target.path), None)
        if not target.path or resource:
            bindings.append(
                {
                    "kind": "skill",
                    "version_id": str(version.id),
                    "skill_id": skill["id"],
                    "checksum": version.checksum_sha256,
                    "path": target.path,
                    "offset": target.offset,
                    "length": target.length,
                    "name": f"{skill['name']} · v{skill['content_version']}"
                    + (f" / {target.path}" if target.path else ""),
                }
            )
    return target, bindings


def _load_skill(*, session, run, operation, state, **_facts):
    from short_drama.schemas.assistant.operations.trace import ExecutionTarget

    arguments = operation.arguments
    known = [
        *run.config_snapshot["skill_catalog"],
        *[item["skill"] for item in state.get("skill_activations", [])],
    ]
    matches = {
        item["id"]: item
        for item in known
        if arguments.get("name", "").removeprefix("skill://")
        in {item.get("id"), item.get("name"), item.get("slug")}
        and str(item["content_version"]) == str(arguments.get("content_version"))
    }
    if len(matches) == 1:
        target, bindings = _skill_target(next(iter(matches.values())), arguments, session)
    else:
        target, bindings = (
            ExecutionTarget(
                kind="query", display_name=utf8_preview(arguments.get("name", ""), 480)
            ),
            [],
        )
    return "加载 Skill", target, bindings


def _skill_resource(*, spec, session, operation, state, **_facts):
    from short_drama.agent.skill.contracts import SKILL_RESOURCE, ReadSkillResource
    from short_drama.schemas.assistant.operations.trace import ExecutionTarget

    arguments = operation.arguments
    if spec.name == SKILL_RESOURCE:
        arguments = {
            **{name: ReadSkillResource.model_fields[name].default for name in ("offset", "limit")},
            **arguments,
        }
    activation = next(
        (
            item
            for item in state.get("skill_activations", [])
            if item["id"] == str(operation.arguments.get("activation_id"))
            and item.get("scope_id") == str(operation.scope_id)
        ),
        None,
    )
    if activation:
        target, bindings = _skill_target(activation["skill"], arguments, session)
    else:
        target, bindings = (
            ExecutionTarget(
                kind="skill_activation",
                id=operation.arguments.get("activation_id"),
                display_name="不可访问的 Skill",
                path=operation.arguments.get("path"),
            ),
            [],
        )
    action = {
        "runtime__release_skill": "释放 Skill",
        "runtime__materialize_skill_resource": "导入 Skill 资源",
    }.get(spec.name, "读取 Skill 资源")
    return action, target, bindings


def _search_skill(*, operation, **_facts):
    from short_drama.schemas.assistant.operations.trace import ExecutionTarget

    return (
        "搜索 Skill",
        ExecutionTarget(
            kind="query", display_name=utf8_preview(operation.arguments.get("query", ""), 480)
        ),
        [],
    )


def _subtask(*, operation, **_facts):
    from short_drama.schemas.assistant.operations.trace import ExecutionTarget

    return (
        "委派子任务",
        ExecutionTarget(
            kind="subtask",
            id=str(operation.tool_call_id),
            display_name=utf8_preview(
                operation.arguments.get("description", "").split("```workbench-inputs", 1)[0], 480
            ),
        ),
        [],
    )


def _workspace(*, spec, operation, **_facts):
    from short_drama.schemas.assistant.operations.trace import ExecutionTarget

    arguments = operation.arguments
    command = spec.name == "execute"
    value = (
        arguments.get("command")
        or arguments.get("file_path")
        or arguments.get("path")
        or "/workspace"
    )
    return (
        "执行命令" if command else "操作工作区",
        ExecutionTarget(
            kind="command" if command else "file",
            id=str(operation.scope_id),
            display_name=utf8_preview(display_safe(value), 480),
            path=arguments.get("file_path") or arguments.get("path"),
        ),
        [],
    )


def _workspace_transfer(*, spec, operation, **_facts):
    from short_drama.schemas.assistant.operations.trace import ExecutionTarget

    importing = spec.name == "runtime__import_resources"
    paths = (
        [item.get("path", "") for item in operation.arguments.get("resources", [])]
        if importing
        else operation.arguments.get("paths", [])
    )
    return (
        "导入资源" if importing else "导出文件",
        ExecutionTarget(
            kind="file",
            id=str(operation.scope_id),
            display_name=utf8_preview("、".join(paths), 480),
            path=paths[0] if len(paths) == 1 else None,
        ),
        [],
    )


_PRESENTATIONS = {
    "runtime__load_skill": _load_skill,
    "runtime__read_skill_resource": _skill_resource,
    "runtime__release_skill": _skill_resource,
    "runtime__materialize_skill_resource": _skill_resource,
    "runtime__search_skills": _search_skill,
    "task": _subtask,
    **dict.fromkeys(
        ("execute", "ls", "read_file", "write_file", "edit_file", "glob", "grep"), _workspace
    ),
    "runtime__import_resources": _workspace_transfer,
    "runtime__export_artifacts": _workspace_transfer,
}


def project_display(*, spec, session, task, run, operation, state):
    """Resolve titles from admitted execution facts, never a model-provided title."""
    from short_drama.schemas.assistant.operations.trace import ExecutionDisplay

    action, target, bindings = _PRESENTATIONS.get(spec.name, _default_target)(
        spec=spec, session=session, task=task, run=run, operation=operation, state=state
    )
    detail = f"《{target.display_name}》"
    if target.version:
        detail += f" · v{target.version}"
    if target.path:
        detail += f" / {target.path}"
    return ExecutionDisplay.model_validate(
        display_safe(
            {"action": action, "target": target.model_dump(mode="json"), "title": action + detail}
        )
    ), bindings
