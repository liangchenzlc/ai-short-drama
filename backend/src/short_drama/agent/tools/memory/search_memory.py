from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.memory.search_memory_description import DESCRIPTION
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import SearchMemory
from short_drama.service.assistant.memory.memory_store import (
    checked_scope,
    require_memory_enabled,
    search_memory_locked,
)


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    require_memory_enabled(session, conversation.owner_user_id)
    scope = checked_scope(session, conversation.owner_user_id)
    return search_memory_locked(session, scope, values.query, limit=values.limit)


TOOL = create_runtime_tool(
    "runtime__search_memory",
    DESCRIPTION,
    SearchMemory,
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
