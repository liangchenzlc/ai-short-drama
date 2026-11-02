from short_drama.agent.memory.commands import execute_memory_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ChangeMemory


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return execute_memory_locked(session, conversation, task, operation, values, settings)


TOOL = create_runtime_tool(
    "runtime__forget_memory",
    "按当前版本遗忘指定记忆，核对真实用户来源并使相关上下文失效。",
    ChangeMemory,
    invoke_locked,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="write",
    source="runtime",
    modes=frozenset(set()),
)
