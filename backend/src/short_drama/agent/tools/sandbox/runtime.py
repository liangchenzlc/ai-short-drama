"""Resource I/O runs outside SQL; identity comes exclusively from ToolRuntime."""

import asyncio
from copy import deepcopy

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

from short_drama.agent.sandbox.resources import SandboxResourceService
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.core.exceptions import WorkflowError


def resource_spec(name, description: str, schema, method, argument, *, skill_state=False):
    async def invoke(runtime: ToolRuntime, **kwargs):
        values = schema.model_validate(kwargs)
        state = deepcopy(runtime.state) if skill_state else None
        context = runtime.context
        if skill_state and state.get("scope_id") != str(context.scope_id):
            raise WorkflowError("assistant_skill_scope", "方法状态与当前执行范围不匹配", 409)
        service = SandboxResourceService(context, runtime.tool_call_id, state=state)
        args = (values,) if skill_state else (context.scope_id, getattr(values, argument))
        worker = asyncio.create_task(asyncio.to_thread(getattr(service, method), *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await context.sandbox.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            raise

    tool = StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=description,
        args_schema=schema.model_json_schema(),
    )
    return ToolSpec(
        name=name,
        description=description,
        args_schema=schema,
        handler=tool,
        effect="write",
        source="runtime",
    )
