from short_drama.agent.memory.reads import read_history_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.memory.read_conversation_history_description import DESCRIPTION
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import ReadHistory


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    return read_history_locked(session, conversation, task, operation, values)


TOOL = create_runtime_tool(
    "runtime__read_conversation_history",
    DESCRIPTION,
    ReadHistory,
    invoke_locked,
)
SPEC = ToolSpec(
    name=TOOL.name,
    description=TOOL.description,
    args_schema=TOOL.input_model,
    handler=TOOL,
    effect="read",
    source="runtime",
    modes=frozenset(set()),
)
