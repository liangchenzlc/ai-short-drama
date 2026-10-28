"""Actor and applied-task boundaries shared by autonomous write handlers."""

from sqlalchemy import select

from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.tools.registry import TOOL_VERSION
from short_drama.core.exceptions import WorkflowError
from short_drama.domain.agent import AgentConversation, AgentMessage, AgentRun
from short_drama.domain.agent_task import AgentTaskInput


def autonomous_scope(session, conversation, task, operation):
    run = session.get(AgentRun, operation.run_id)
    owner = session.scalar(
        select(AgentConversation.owner_user_id).where(
            AgentConversation.id == task.conversation_id,
            AgentConversation.status == "active",
        )
    )
    if (
        run is None
        or run.task_id != task.id
        or run.initiated_by != owner
        or owner != conversation.owner_user_id
        or operation.tool_version not in {2, 3, TOOL_VERSION}
        or (run.checkpoint or {}).get("engine") != ENGINE_VERSION
    ):
        raise WorkflowError("assistant_tool_scope", "工具不属于当前可信任务", 403)
    source = session.execute(
        select(AgentMessage, AgentTaskInput)
        .join(
            AgentTaskInput,
            AgentTaskInput.message_id == AgentMessage.id,
        )
        .where(
            AgentTaskInput.task_id == task.id,
            AgentTaskInput.input_revision <= operation.input_revision,
            AgentTaskInput.applied_plan_revision.is_not(None),
            AgentMessage.role == "user",
            AgentMessage.conversation_id == conversation.id,
        )
        .order_by(AgentTaskInput.input_revision.desc())
        .limit(1)
    ).one_or_none()
    restricted, _ = restricted_messages(session, conversation.id)
    if source is None or source[0].id in restricted:
        raise WorkflowError("assistant_input_scope", "当前任务没有可用的已应用用户输入", 403)
    return {"message": source[0], "input": source[1], "snapshot": task.context_snapshot}
