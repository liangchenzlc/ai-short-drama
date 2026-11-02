"""Bind real runtime tools to one short transaction per framework invocation."""

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select

from short_drama.agent.core.access import LeaseLost, lock_run, may_decide
from short_drama.agent.core.fence import owns_execution
from short_drama.agent.task.queries import task_for_run
from short_drama.agent.tools.scope import task_tool_scope
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Project
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.service.identity.authentication.actor import business_actor_locked


class RuntimeTool(StructuredTool):
    locked_handler: Callable[..., dict] = Field(exclude=True, repr=False)
    input_model: type[BaseModel] = Field(exclude=True, repr=False)
    skill_state: bool = False


def create_runtime_tool(
    name: str,
    description: str,
    args_schema: type[BaseModel],
    handler: Callable[..., dict],
    *,
    prepare: Callable[..., dict] | None = None,
    skill_state: bool = False,
) -> RuntimeTool:
    async def invoke(runtime: ToolRuntime, **kwargs: Any) -> dict:
        values = args_schema.model_validate(kwargs)
        state = deepcopy(runtime.state) if skill_state else None
        if skill_state and state.get("scope_id") != str(runtime.context.scope_id):
            raise WorkflowError("assistant_skill_scope", "方法状态与当前执行范围不匹配", 409)
        if prepare is not None:
            prepared = await asyncio.to_thread(
                prepare,
                values,
                runtime.context,
                runtime.tool_call_id,
                **({"state": state} if skill_state else {}),
            )
            result = await asyncio.to_thread(
                invoke_runtime_tool,
                handler,
                values,
                runtime.context,
                runtime.tool_call_id,
                resource_prepared=prepared,
                state=state,
            )
        else:
            result = await asyncio.to_thread(
                invoke_runtime_tool,
                handler,
                values,
                runtime.context,
                runtime.tool_call_id,
                state=state,
            )
        if not skill_state:
            return result
        previous = {item["id"]: item for item in runtime.state.get("skill_activations", [])}
        return Command(
            update={
                "skill_activations": [
                    item
                    for item in state.get("skill_activations", [])
                    if previous.get(item["id"]) != item
                ],
                "messages": [
                    ToolMessage(
                        content=json.dumps(result, ensure_ascii=False),
                        name=name,
                        tool_call_id=runtime.tool_call_id,
                        additional_kwargs={
                            "skill_activation_ids": [result["activation_id"]]
                            if result.get("activation_id")
                            else [],
                            "source_message_ids": [
                                item["source_message_id"]
                                for item in state.get("skill_activations", [])
                                if item["id"] == result.get("activation_id")
                            ],
                        },
                    )
                ],
            }
        )

    return RuntimeTool.from_function(
        coroutine=invoke,
        name=name,
        description=description,
        args_schema=args_schema.model_json_schema(),
        locked_handler=handler,
        input_model=args_schema,
        skill_state=skill_state,
    )


def execution_locked(session, context, tool_call_id):
    target = (
        session.scalar(
            select(AgentTask.execution_target)
            .join(AgentRun, AgentRun.task_id == AgentTask.id)
            .where(AgentRun.id == context.run_id)
        )
        or {}
    )
    if target.get("project_id"):
        # 全局会话没有 project_id，仍须先锁可信任务目标，再锁 Run 与账号。
        session.scalar(
            select(Project.id).where(Project.id == int(target["project_id"])).with_for_update()
        )
    rows = lock_run(session, context.run_id)
    if rows is None:
        raise WorkflowError("assistant_execution_scope", "执行范围不存在", 403)
    project, conversation, run = rows
    if (
        conversation.owner_user_id != context.owner_id
        or run.initiated_by != context.owner_id
        or not context.fence
        or not context.fence[1]
        or not owns_execution(run, context.fence)
        or not may_decide(session, project, conversation, run)
    ):
        raise LeaseLost()
    task = task_for_run(session, run)
    call = session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.run_id == run.id,
            AgentToolCall.scope_id == context.scope_id,
            AgentToolCall.provider_call_id == tool_call_id,
        )
    )
    operation = (
        session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id,
                AgentOperation.scope_id == context.scope_id,
                AgentOperation.tool_call_id == call.id,
            )
        )
        if call is not None
        else None
    )
    if operation is None:
        raise WorkflowError("assistant_tool_call_scope", "工具调用尚未由执行边界登记", 409)
    session.info["actor"] = business_actor_locked(session, context.owner_id, tool_call_id)
    session.info["tool_lookup"] = context.tool_lookup
    task_tool_scope(session, conversation, task, operation)
    return conversation, task, run, operation


def invoke_runtime_tool(
    handler, values, context, tool_call_id, *, resource_prepared=None, state=None
):
    with context.factory.begin() as session:
        conversation, task, _run, operation = execution_locked(session, context, tool_call_id)
        if resource_prepared is not None and resource_prepared["fence"] != context.fence:
            raise LeaseLost()
        return handler(
            session,
            conversation,
            task,
            operation,
            values,
            context.settings,
            resource_prepared=resource_prepared,
            **({"state": state, "tool_lookup": context.tool_lookup} if state is not None else {}),
        )
