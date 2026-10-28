"""What the user is allowed to see for each assistant tool.

The frontend must not keep its own tool-name allowlist: it drifts from the executor
and leaks internal steps. Visibility and the user-facing label are decided here and
travel with every step event and snapshot.

``silent``   internal reads (skills, memory, receipts); never rendered.
``step``     one line in a collapsed group, no arguments or payloads.
``artifact`` a produced work item; always rendered on its own.
"""

SILENT = "silent"
STEP = "step"
ARTIFACT = "artifact"
VISIBILITIES = frozenset({SILENT, STEP, ARTIFACT})

_VISIBILITY = {
    "read_current_script": STEP,
    "propose_script_patch": ARTIFACT,
    "search_skills": SILENT,
    "load_skill": STEP,
    "read_skill_resource": STEP,
    "read_operation_receipt": SILENT,
    "get_generation_policy": SILENT,
    "search_memories": SILENT,
    "read_memories": SILENT,
    "memory_catalog": SILENT,
    "search_memory": SILENT,
    "read_memory": SILENT,
    "read_conversation_history": SILENT,
    "remember_memory": SILENT,
    "update_memory": SILENT,
    "forget_memory": SILENT,
    "restore_memory": SILENT,
    "describe_creation_target": STEP,
    "read_selected_subjects": STEP,
    "read_task_results": STEP,
    "read_image_group": STEP,
    "read_image_content": STEP,
    "get_generation_status": STEP,
    "apply_standard_patch": ARTIFACT,
    "apply_canvas_patch": ARTIFACT,
    "submit_generation_batch": ARTIFACT,
    "set_image_group_primary": ARTIFACT,
}

_LABELS = {
    "read_current_script": "读取当前剧本",
    "propose_script_patch": "准备剧本修改建议",
    "load_skill": "读取创作方法",
    "read_skill_resource": "读取创作方法",
    "describe_creation_target": "核对写入目标",
    "confirm_requirements": "确认交付要求",
    "read_selected_subjects": "读取选定内容",
    "read_task_results": "读取作品内容",
    "read_image_group": "读取图片组",
    "read_image_content": "读取图片",
    "get_generation_status": "查询生成进度",
    "apply_standard_patch": "保存到正文",
    "apply_canvas_patch": "更新画布",
    "submit_generation_batch": "生成媒体",
    "set_image_group_primary": "设置主图",
}

_FALLBACK_LABEL = "执行创作操作"


def visibility(tool_name: str) -> str:
    """Unknown tools stay visible as a generic step rather than disappearing."""
    return _VISIBILITY.get(tool_name, STEP)


def label(tool_name: str) -> str:
    return _LABELS.get(tool_name, _FALLBACK_LABEL)


def is_silent(tool_name: str) -> bool:
    return visibility(tool_name) == SILENT
