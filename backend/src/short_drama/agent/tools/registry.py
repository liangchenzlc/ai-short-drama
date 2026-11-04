"""Explicit authoritative directory. Tool definitions never import this aggregator."""

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache

from short_drama.agent.tools.ask_user.ask_user import SPEC as RUNTIME__ASK_USER
from short_drama.agent.tools.canvas.apply_canvas_patch import SPEC as WORKBENCH__APPLY_CANVAS_PATCH
from short_drama.agent.tools.context.record_task_observation import (
    SPEC as RUNTIME__RECORD_TASK_OBSERVATION,
)
from short_drama.agent.tools.contracts import ToolSpec, require_tool_description
from short_drama.agent.tools.delegation.task import SPEC as TASK
from short_drama.agent.tools.media_generation.get_generation_policy import (
    SPEC as RUNTIME__GET_GENERATION_POLICY,
)
from short_drama.agent.tools.media_generation.get_generation_status import (
    SPEC as WORKBENCH__GET_GENERATION_STATUS,
)
from short_drama.agent.tools.media_generation.submit_generation_batch import (
    SPEC as WORKBENCH__SUBMIT_GENERATION_BATCH,
)
from short_drama.agent.tools.media_generation.wait_generation import (
    SPEC as RUNTIME__WAIT_GENERATION,
)
from short_drama.agent.tools.memory.forget_memory import SPEC as RUNTIME__FORGET_MEMORY
from short_drama.agent.tools.memory.memory_catalog import SPEC as RUNTIME__MEMORY_CATALOG
from short_drama.agent.tools.memory.read_conversation_history import (
    SPEC as RUNTIME__READ_CONVERSATION_HISTORY,
)
from short_drama.agent.tools.memory.read_memory import SPEC as RUNTIME__READ_MEMORY
from short_drama.agent.tools.memory.remember_memory import SPEC as RUNTIME__REMEMBER_MEMORY
from short_drama.agent.tools.memory.restore_memory import SPEC as RUNTIME__RESTORE_MEMORY
from short_drama.agent.tools.memory.search_memory import SPEC as RUNTIME__SEARCH_MEMORY
from short_drama.agent.tools.memory.update_memory import SPEC as RUNTIME__UPDATE_MEMORY
from short_drama.agent.tools.presentation import tool_trace_kind
from short_drama.agent.tools.projects.list_projects import SPEC as WORKBENCH__LIST_PROJECTS
from short_drama.agent.tools.projects.read_project import SPEC as WORKBENCH__READ_PROJECT
from short_drama.agent.tools.resources.describe_creation_target import (
    SPEC as RUNTIME__DESCRIBE_CREATION_TARGET,
)
from short_drama.agent.tools.resources.read_image_content import (
    SPEC as WORKBENCH__READ_IMAGE_CONTENT,
)
from short_drama.agent.tools.resources.read_image_group import SPEC as WORKBENCH__READ_IMAGE_GROUP
from short_drama.agent.tools.resources.read_operation_receipt import (
    SPEC as RUNTIME__READ_OPERATION_RECEIPT,
)
from short_drama.agent.tools.resources.read_resource import SPEC as RUNTIME__READ_RESOURCE
from short_drama.agent.tools.resources.read_selected_subjects import (
    SPEC as RUNTIME__READ_SELECTED_SUBJECTS,
)
from short_drama.agent.tools.resources.read_task_results import SPEC as RUNTIME__READ_TASK_RESULTS
from short_drama.agent.tools.resources.set_image_group_primary import (
    SPEC as WORKBENCH__SET_IMAGE_GROUP_PRIMARY,
)
from short_drama.agent.tools.sandbox.edit_file import SPEC as EDIT_FILE
from short_drama.agent.tools.sandbox.execute import SPEC as EXECUTE
from short_drama.agent.tools.sandbox.export_artifacts import SPEC as RUNTIME__EXPORT_ARTIFACTS
from short_drama.agent.tools.sandbox.glob import SPEC as GLOB
from short_drama.agent.tools.sandbox.grep import SPEC as GREP
from short_drama.agent.tools.sandbox.import_resources import SPEC as RUNTIME__IMPORT_RESOURCES
from short_drama.agent.tools.sandbox.ls import SPEC as LS
from short_drama.agent.tools.sandbox.materialize_skill_resource import (
    SPEC as RUNTIME__MATERIALIZE_SKILL_RESOURCE,
)
from short_drama.agent.tools.sandbox.read_file import SPEC as READ_FILE
from short_drama.agent.tools.sandbox.write_file import SPEC as WRITE_FILE
from short_drama.agent.tools.skill.load_skill import SPEC as RUNTIME__LOAD_SKILL
from short_drama.agent.tools.skill.read_skill_resource import SPEC as RUNTIME__READ_SKILL_RESOURCE
from short_drama.agent.tools.skill.release_skill import SPEC as RUNTIME__RELEASE_SKILL
from short_drama.agent.tools.skill.search_skills import SPEC as RUNTIME__SEARCH_SKILLS
from short_drama.agent.tools.standard.apply_standard_patch import (
    SPEC as WORKBENCH__APPLY_STANDARD_PATCH,
)
from short_drama.agent.tools.standard.propose_script_patch import (
    SPEC as RUNTIME__PROPOSE_SCRIPT_PATCH,
)
from short_drama.agent.tools.standard.read_assets import SPEC as WORKBENCH__READ_ASSETS
from short_drama.agent.tools.standard.read_current_script import (
    SPEC as WORKBENCH__READ_CURRENT_SCRIPT,
)
from short_drama.agent.tools.standard.read_storyboard import SPEC as WORKBENCH__READ_STORYBOARD
from short_drama.agent.tools.trace_visibility import tool_trace_visibility


def build_catalog(specs: Iterable[ToolSpec]) -> dict[str, ToolSpec]:
    result = {}
    for spec in specs:
        require_tool_description(spec.name, spec.description)
        if spec.name in result:
            raise ValueError(f"重复工具名称：{spec.name}")
        result[spec.name] = spec
    return result


TOOL_SPECS = (
    WORKBENCH__LIST_PROJECTS,
    WORKBENCH__READ_PROJECT,
    WORKBENCH__READ_ASSETS,
    WORKBENCH__READ_STORYBOARD,
    TASK,
    RUNTIME__ASK_USER,
    RUNTIME__WAIT_GENERATION,
    WORKBENCH__READ_CURRENT_SCRIPT,
    WORKBENCH__APPLY_STANDARD_PATCH,
    WORKBENCH__APPLY_CANVAS_PATCH,
    WORKBENCH__SUBMIT_GENERATION_BATCH,
    WORKBENCH__GET_GENERATION_STATUS,
    WORKBENCH__READ_IMAGE_GROUP,
    WORKBENCH__READ_IMAGE_CONTENT,
    WORKBENCH__SET_IMAGE_GROUP_PRIMARY,
    RUNTIME__PROPOSE_SCRIPT_PATCH,
    RUNTIME__GET_GENERATION_POLICY,
    RUNTIME__DESCRIBE_CREATION_TARGET,
    RUNTIME__READ_SELECTED_SUBJECTS,
    RUNTIME__READ_TASK_RESULTS,
    RUNTIME__READ_OPERATION_RECEIPT,
    RUNTIME__READ_RESOURCE,
    RUNTIME__SEARCH_SKILLS,
    RUNTIME__LOAD_SKILL,
    RUNTIME__READ_SKILL_RESOURCE,
    RUNTIME__RELEASE_SKILL,
    RUNTIME__MEMORY_CATALOG,
    RUNTIME__SEARCH_MEMORY,
    RUNTIME__READ_MEMORY,
    RUNTIME__REMEMBER_MEMORY,
    RUNTIME__UPDATE_MEMORY,
    RUNTIME__FORGET_MEMORY,
    RUNTIME__RESTORE_MEMORY,
    RUNTIME__READ_CONVERSATION_HISTORY,
    RUNTIME__RECORD_TASK_OBSERVATION,
    EXECUTE,
    LS,
    READ_FILE,
    WRITE_FILE,
    EDIT_FILE,
    GLOB,
    GREP,
    RUNTIME__IMPORT_RESOURCES,
    RUNTIME__EXPORT_ARTIFACTS,
    RUNTIME__MATERIALIZE_SKILL_RESOURCE,
)
TOOL_SPECS = tuple(
    replace(
        spec,
        trace_kind=tool_trace_kind(spec.name),
        trace_visibility=tool_trace_visibility(spec.name),
    )
    for spec in TOOL_SPECS
)
_CATALOG = build_catalog(TOOL_SPECS)


def get_spec(name: str) -> ToolSpec:
    return _CATALOG[name]


def available_specs(
    task, *, media_enabled: bool, workbench_enabled: bool = True, memory_enabled: bool = True
) -> tuple[ToolSpec, ...]:
    mode = (task.execution_target or {}).get("mode")
    capabilities = {
        "none": True,
        "workbench_write": workbench_enabled,
        "media_generate": workbench_enabled and media_enabled,
        "memory_read": memory_enabled,
        "memory_write": memory_enabled,
    }
    return tuple(
        spec
        for spec in TOOL_SPECS
        if spec.expose_assistant
        and (spec.source != "business" or bool(mode))
        and (not spec.modes or mode in spec.modes)
        and capabilities[spec.required_capability]
    )


@lru_cache(maxsize=64)
def _schema_manifest(names: tuple[str, ...]) -> tuple[dict, ...]:
    return tuple(
        {
            "name": name,
            "description": get_spec(name).description,
            "parameters": get_spec(name).args_schema
            if isinstance(get_spec(name).args_schema, dict)
            else get_spec(name).args_schema.model_json_schema(),
        }
        for name in names
    )


def tool_manifest(
    task, *request_catalogs, media_enabled: bool = True, progressive: bool = False, **capabilities
) -> list[dict]:
    return deepcopy(
        list(
            _schema_manifest(
                tuple(
                    spec.name
                    for spec in available_specs(task, media_enabled=media_enabled, **capabilities)
                )
            )
        )
    )


def tool_catalog(
    task, *, media_enabled: bool, workbench_enabled: bool = True, memory_enabled: bool = True
) -> list[dict]:
    return [
        {"name": spec.name, "description": spec.description, "effect": spec.effect}
        for spec in available_specs(
            task,
            media_enabled=media_enabled,
            workbench_enabled=workbench_enabled,
            memory_enabled=memory_enabled,
        )
    ]
