"""Wait for previously admitted native tasks; never invoke submission."""

import asyncio
from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import Field, model_validator

from short_drama.agent.tools.contracts import ToolSpec
from short_drama.schemas.base import Identifier, InputModel


class WaitGenerationRequest(InputModel):
    task_ids: Annotated[list[Identifier], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def unique_ids(self):
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("生成任务身份不能重复")
        return self


async def wait_generation(runtime: ToolRuntime, **kwargs: Any):
    values = WaitGenerationRequest.model_validate(kwargs)
    context = runtime.context
    wait = await asyncio.to_thread(
        context.waits.inspect_and_register,
        run_id=context.run_id,
        scope_id=context.scope_id,
        tool_call_id=runtime.tool_call_id,
        task_ids=values.task_ids,
        fence=context.fence,
    )
    if wait.satisfied_without_interrupt:
        return wait.result
    interrupt({"kind": "generation", "wait_operation_id": str(wait.operation_id)})
    return await asyncio.to_thread(
        context.waits.read_result, wait.operation_id, fence=context.fence
    )


runtime__wait_generation = StructuredTool.from_function(
    coroutine=wait_generation,
    name="runtime__wait_generation",
    description="下一步需要生成结果时等待；已完成任务立即返回，不重新提交。",
    args_schema=WaitGenerationRequest.model_json_schema(),
)
SPEC = ToolSpec(
    name=runtime__wait_generation.name,
    description=runtime__wait_generation.description,
    args_schema=WaitGenerationRequest,
    handler=runtime__wait_generation,
    effect="wait",
    source="runtime",
)
