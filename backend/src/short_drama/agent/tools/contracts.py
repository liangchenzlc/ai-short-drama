"""Single tool definition contract; injected identity never belongs to model arguments."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from short_drama.agent.tools.presentation import project_display
from short_drama.schemas.assistant.operations.trace import ToolTraceVisibility

ToolEffect = Literal[
    "read", "write", "submit", "context", "wait", "ask_user", "execute", "delegate"
]
ToolCapability = Literal["none", "workbench_write", "media_generate", "memory_read", "memory_write"]
TOOL_VERSION = 5


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel] | dict[str, Any]
    handler: Callable[..., Any] | BaseTool
    effect: ToolEffect = "read"
    required_capability: ToolCapability = "none"
    expose_mcp: bool = False
    expose_assistant: bool = True
    source: Literal["business", "runtime"] = "business"
    modes: frozenset[str] = frozenset()
    presentation: Callable[..., Any] = project_display
    trace_kind: Literal["tool", "skill", "sandbox", "subtask", "wait"] = "tool"
    trace_visibility: ToolTraceVisibility = ToolTraceVisibility.VISIBLE


CANVAS_TOOL = "workbench__apply_canvas_patch"
IMAGE_READ_TOOL = "workbench__read_image_group"
IMAGE_CONTENT_TOOL = "workbench__read_image_content"
IMAGE_PRIMARY_TOOL = "workbench__set_image_group_primary"
MEDIA_TOOL = "workbench__submit_generation_batch"
MEDIA_STATUS_TOOL = "workbench__get_generation_status"
STANDARD_TOOL = "workbench__apply_standard_patch"
STANDARD_READ_TOOL = "runtime__read_task_results"
MEMORY_READ_LIMIT = 20000
MEMORY_WRITE_TOOLS = frozenset(
    {
        "runtime__remember_memory",
        "runtime__update_memory",
        "runtime__forget_memory",
        "runtime__restore_memory",
    }
)
