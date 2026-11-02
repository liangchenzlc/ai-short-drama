from short_drama.agent.skill import progressive


def load_locked(session, conversation, task, operation, values, settings, *, state, tool_lookup):
    return progressive.load_locked(
        session,
        conversation,
        task,
        operation,
        values,
        settings,
        state=state,
        tool_lookup=tool_lookup,
    )


def search_locked(session, conversation, task, operation, values, settings, *, state):
    from short_drama.domain.assistant.execution.models import AgentRun

    return progressive.catalog_locked(
        session,
        conversation.owner_user_id,
        task,
        settings,
        state=state,
        run=session.get(AgentRun, operation.run_id),
        query=values.query,
        offset=values.offset,
        limit=values.limit,
        include_manual=False,
        search=True,
    )
