"""Business regression fixtures use the production graph and tool admission services."""

import asyncio
from copy import deepcopy

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool
from sqlalchemy import select

from short_drama.agent.context.business_scope import assistant_invocation_locked
from short_drama.agent.core import ENGINE_VERSION
from short_drama.agent.core.access import LeaseLost
from short_drama.agent.gateway.langchain import build_chat_model
from short_drama.agent.resources.graph import GraphResources
from short_drama.agent.runtime.agent import build_agent
from short_drama.agent.runtime.middleware import ContextMiddleware, ExecutionMiddleware
from short_drama.agent.runtime.model_ledger import RunModelLedger
from short_drama.agent.tools.langchain import create_langchain_tool
from short_drama.agent.tools.registry import get_spec
from short_drama.agent.tools.scope import task_tool_scope
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.service.assistant.operations.invocation import BusinessInvocationService
from tests.support.assistant_runtime import RuntimeTransport, calls


def scope_graph(workspace, accepted, requests, *, context=None, before_tool=None):
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime

    saver, context = runtime(workspace, accepted) if context is None else (None, context)
    if saver is None:
        from short_drama.agent.runtime.checkpointer import MySQLCheckpointSaver
        from short_drama.service.assistant.execution.graph import AgentGraphService

        saver = MySQLCheckpointSaver(
            AgentGraphService(
                context.factory,
                owner_id=context.owner_id,
                task_id=context.task_id,
                run_id=context.run_id,
                version=ENGINE_VERSION,
                fence=context.fence,
            )
        )
    with context.factory() as session:
        snapshot = session.get(AgentRun, context.run_id).config_snapshot
        previous = set(session.scalars(select(AgentOperation.id)))
    transport = RuntimeTransport(
        [*[calls(request) for request in requests], {"text": "受控业务调用已结算"}]
    )
    resources = GraphResources(context, snapshot, None)
    ledger = RunModelLedger(context)
    model = build_chat_model(
        snapshot, "", callbacks=ledger.callbacks(hydrate=resources.hydrate), transport=transport
    )

    class Boundary(AgentMiddleware):
        applied = False

        async def awrap_tool_call(self, request, handler):
            if before_tool is not None and not self.applied:
                self.applied = True
                await asyncio.to_thread(before_tool)
            return await handler(request)

    graph = build_agent(
        model=model,
        tools=[
            create_langchain_tool(get_spec(name)) for name in dict.fromkeys(r[1] for r in requests)
        ],
        middleware=[ContextMiddleware(), ExecutionMiddleware(resources), Boundary()],
        checkpointer=saver,
        system_prompt="按用户真实输入执行已选业务工具，读取和版本边界由正式Service验证。",
    )
    try:
        asyncio.run(
            graph.ainvoke(
                {"messages": []},
                {"configurable": {"thread_id": saver.thread_id}, "recursion_limit": 1000},
                context=context,
            )
        )
    except LeaseLost:
        with context.factory() as session:
            if not session.get(AgentRun, context.run_id).cancel_requested:
                raise
    finally:
        resources.close()
    with context.factory() as session:
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.run_id == context.run_id, AgentOperation.id.not_in(previous))
            .order_by(AgentOperation.id)
        ).all()
    return context, transport, operations


def invoke_tool_locked(
    session, conversation, task, operation, *, settings=None, resource_prepared=None
):
    """Probe a concrete Service inside a test-owned transaction, without an execution loop."""
    spec = get_spec(operation.tool_name)
    task_tool_scope(session, conversation, task, operation)
    values = spec.args_schema.model_validate(operation.arguments)
    if isinstance(spec.handler, BaseTool):
        return spec.handler.locked_handler(
            session,
            conversation,
            task,
            operation,
            values,
            settings,
            resource_prepared=resource_prepared,
        )
    identity = assistant_invocation_locked(
        session, conversation, task, session.get(AgentRun, operation.run_id), operation
    )
    result = BusinessInvocationService(session, settings).execute_locked(
        identity, spec.name, values, spec.handler, mutating=spec.effect in {"write", "submit"}
    )
    if result.get("business_invocation_id"):
        operation.business_invocation_id = int(result["business_invocation_id"])
        operation.changes = deepcopy(
            session.get(BusinessToolInvocation, operation.business_invocation_id).changes
        )
    operation.result_refs = result.get("result_refs", [])
    return result
