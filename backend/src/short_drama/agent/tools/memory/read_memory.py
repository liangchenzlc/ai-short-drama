from short_drama.agent.memory.reads import read_memory_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ReadMemory


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return read_memory_locked(session, conversation, task, operation, values)


TOOL = create_runtime_tool(
    "runtime__read_memory",
    "按真实 ID 和版本读取记忆正文，支持分页，保留遗忘来源及任务总字符预算。",
    ReadMemory,
    invoke_locked,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="read",
    required_capability="memory_read",
    source="runtime",
    modes=frozenset(set()),
)
