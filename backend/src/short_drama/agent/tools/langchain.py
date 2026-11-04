"""Framework injection is hidden from the one business argument schema."""

import asyncio
from copy import deepcopy
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool

from short_drama.agent.context.business_scope import assistant_invocation_locked
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.runtime import execution_locked
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.service.assistant.operations.invocation import BusinessInvocationService


def create_langchain_tool(spec: ToolSpec, *, description: str | None = None) -> BaseTool:
    description = spec.description if description is None else description
    if isinstance(spec.handler, BaseTool):
        return spec.handler.model_copy(update={"description": description})

    async def invoke(runtime: ToolRuntime, **kwargs: Any) -> dict:
        return await invoke_business_tool(spec, kwargs, runtime.context, runtime.tool_call_id)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=spec.name,
        description=description,
        args_schema=spec.args_schema.model_json_schema(),
    )


async def invoke_business_tool(spec, arguments, context, tool_call_id):
    values = spec.args_schema.model_validate(arguments)
    return await asyncio.to_thread(_invoke_business_tool, spec, values, context, tool_call_id)


def _invoke_business_tool(spec, values, context, tool_call_id):
    with context.factory.begin() as session:
        conversation, task, run, operation = execution_locked(session, context, tool_call_id)
        identity = assistant_invocation_locked(session, conversation, task, run, operation)
        result = BusinessInvocationService(session, context.settings).execute_locked(
            identity,
            spec.name,
            values,
            spec.handler,
            mutating=spec.effect in {"write", "submit"},
        )
        operation.business_invocation_id = (
            int(result["business_invocation_id"]) if result.get("business_invocation_id") else None
        )
        operation.result_refs = result.get("result_refs", [])
        if operation.business_invocation_id:
            receipt = session.get(BusinessToolInvocation, operation.business_invocation_id)
            operation.changes = deepcopy(receipt.changes)
        return result
