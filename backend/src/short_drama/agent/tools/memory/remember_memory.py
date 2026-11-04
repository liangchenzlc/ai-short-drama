from short_drama.agent.memory.commands import execute_memory_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import RememberMemory


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return execute_memory_locked(session, conversation, task, operation, values, settings)


TOOL = create_runtime_tool(
    "runtime__remember_memory",
    "保存用户明确表达的账号长期偏好；来源必须是本任务已应用的用户消息。",
    RememberMemory,
    invoke_locked,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="write",
    required_capability="memory_write",
    source="runtime",
    modes=frozenset(set()),
)
