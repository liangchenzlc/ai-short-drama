"""Tool classes and user-quote validation shared without importing the contract."""

READ_TOOLS = frozenset(
    {
        "read_resource",
        "read_current_script",
        "read_selected_subjects",
        "read_task_results",
        "read_skill_resource",
        "read_image_group",
        "read_image_content",
        "read_memory",
        "read_conversation_history",
        "describe_creation_target",
    }
)

WRITE_TOOLS = frozenset(
    {
        "apply_standard_patch",
        "propose_script_patch",
        "apply_canvas_patch",
        "submit_generation_batch",
        "set_image_group_primary",
    }
)


def source_valid(item, messages, restricted) -> bool:
    """The cited user text must exist, be applied and not be forgotten."""
    source = messages.get(str(item.source_message_id))
    return bool(
        source
        and int(item.source_message_id) not in restricted
        and item.source_quote.strip()
        and item.source_quote in source.content
    )
