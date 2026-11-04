from short_drama.schemas.assistant.operations.trace import ToolTraceVisibility

_HIDDEN_TOOLS = frozenset(
    {
        "runtime__search_skills",
        "runtime__read_operation_receipt",
        "runtime__get_generation_policy",
        "search_memories",
        "read_memories",
        "runtime__memory_catalog",
        "runtime__search_memory",
        "runtime__read_memory",
        "runtime__remember_memory",
        "runtime__update_memory",
        "runtime__forget_memory",
        "runtime__restore_memory",
        "runtime__read_conversation_history",
    }
)


def tool_trace_visibility(tool_name: str) -> ToolTraceVisibility:
    return ToolTraceVisibility.HIDDEN if tool_name in _HIDDEN_TOOLS else ToolTraceVisibility.VISIBLE
