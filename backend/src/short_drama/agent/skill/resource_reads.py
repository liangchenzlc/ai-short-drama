from short_drama.agent.skill.runtime import finish_resource_locked, prepare_resource_read
from short_drama.core.exceptions import WorkflowError


def prepare_skill_resource(values, context, tool_call_id, *, state):
    from short_drama.agent.tools.runtime import execution_locked

    with context.factory.begin() as session:
        _conversation, _task, _run, operation = execution_locked(session, context, tool_call_id)
        operation_id = operation.id
    return prepare_resource_read(
        context.factory,
        context.settings,
        context.run_id,
        operation_id,
        context.fence,
        values=values,
        state=state,
    )


def read_skill_resource_locked(
    session,
    conversation,
    task,
    operation,
    values,
    settings,
    *,
    state,
    tool_lookup,
    resource_prepared,
):
    if not resource_prepared:
        raise WorkflowError(
            "assistant_skill_read_unprepared", "资料读取尚未准备，请使用原调用重试", 409
        )
    if resource_prepared.get("error"):
        raise resource_prepared["error"]
    return finish_resource_locked(
        session, conversation, task, operation, values, resource_prepared, state=state
    )
