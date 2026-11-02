"""The real question tool; strict DTO validation precedes any durable registration."""

import asyncio
import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.assistant.operations.questions import AskUserRequest


async def ask_user(runtime: ToolRuntime, **kwargs: Any):
    values = AskUserRequest.model_validate(kwargs)
    context = runtime.context
    wait = await asyncio.to_thread(
        context.questions.register,
        run_id=context.run_id,
        scope_id=context.scope_id,
        tool_call_id=runtime.tool_call_id,
        request=values,
        fence=context.fence,
    )
    interrupt({"kind": "user_input", "wait_operation_id": str(wait.operation_id)})
    result = await asyncio.to_thread(
        context.questions.read_answer, wait.operation_id, fence=context.fence
    )
    return ToolMessage(
        content=json.dumps(result, ensure_ascii=False),
        name="runtime__ask_user",
        tool_call_id=runtime.tool_call_id,
        additional_kwargs={
            "source_message_ids": result["source_message_ids"],
            "answer_message_id": result["answer_message_id"],
        },
    )


runtime__ask_user = StructuredTool.from_function(
    coroutine=ask_user,
    name="runtime__ask_user",
    description=(
        "缺少关键输入或必要选择时向用户提问，可一次提出多个单选或多选问题。"
        "每题提供2–4个具体选项，根据任务推荐选项；界面自动提供其他回答，不要将其他写入选项。"
        "不要用它发送普通工作说明。"
    ),
    args_schema=AskUserRequest.model_json_schema(),
)
SPEC = ToolSpec(
    name=runtime__ask_user.name,
    description=runtime__ask_user.description,
    args_schema=AskUserRequest,
    handler=runtime__ask_user,
    effect="ask_user",
    source="runtime",
)
