from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import SearchMemory
from short_drama.service.assistant.memory.memory_store import checked_scope, search_memory_locked


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    scope = checked_scope(
        session, conversation.owner_user_id, conversation.id, task.memory_project_ids
    )
    return search_memory_locked(session, scope, values.query, limit=values.limit)


TOOL = create_runtime_tool(
    "runtime__search_memory",
    "在获准账号、会话和明确绑定作品记忆内检索关键词。",
    SearchMemory,
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
