AGENT_PRIVATE_TABLES = frozenset(
    {
        "agent_conversations",
        "agent_messages",
        "agent_discussion_revisions",
        "agent_batch_undos",
        "agent_runs",
        "agent_turns",
        "agent_tool_calls",
        "agent_events",
        "agent_tasks",
        "agent_task_inputs",
        "agent_task_controls",
        "agent_operations",
        "agent_task_requirements",
        "agent_memory_items",
        "agent_memory_revisions",
        "agent_memory_sources",
        "agent_memory_forget_rules",
        "agent_conversation_summaries",
        "agent_memory_jobs",
        "agent_preferences",
        "agent_media_requests",
        "agent_graph_checkpoints",
        "agent_graph_writes",
    }
)


AGENT_TABLES = AGENT_PRIVATE_TABLES | {
    "agent_artifacts",
    "agent_attachments",
    "agent_skills",
    "agent_skill_versions",
    "agent_skill_settings",
}
