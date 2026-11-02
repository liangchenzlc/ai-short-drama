from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import create_runtime_tool
from short_drama.schemas.assistant.operations.tool_inputs import MemoryCatalog
from short_drama.service.assistant.memory.memory_store import checked_scope, memory_catalog_locked


def invoke_locked(
    session, conversation, task, operation, values, settings, *, resource_prepared=None
):
    scope = checked_scope(
        session, conversation.owner_user_id, conversation.id, task.memory_project_ids
    )
    return memory_catalog_locked(session, scope, offset=values.offset, limit=values.limit)


TOOL = create_runtime_tool(
    "runtime__memory_catalog",
    "分页查看获准记忆目录及真实版本，不读取正文。",
    MemoryCatalog,
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
