"""One Worker delivery runs or resumes the complete framework graph."""

import asyncio
import logging
from dataclasses import replace

from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from short_drama.agent.core import ENGINE_VERSION
from short_drama.agent.core.access import LeaseLost
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.gateway.langchain import build_chat_model
from short_drama.agent.resources.graph import GraphResources
from short_drama.agent.runtime.agent import (
    assistant_middleware,
    build_agent,
    delegation_middleware,
    invalid_tool_selection,
)
from short_drama.agent.runtime.checkpointer import MySQLCheckpointSaver
from short_drama.agent.runtime.context import CHILD_EXCLUDED_TOOLS, RuntimeContext
from short_drama.agent.runtime.events import AgentEventAdapter
from short_drama.agent.runtime.lease import LeaseRenewalUnavailable, run_with_lease
from short_drama.agent.runtime.middleware import (
    ContextMiddleware,
    ExecutionMiddleware,
    TaskScopeMiddleware,
)
from short_drama.agent.runtime.model_ledger import RunModelLedger
from short_drama.agent.runtime.waiting import RuntimeQuestions
from short_drama.agent.sandbox.backend import ScopedBackend
from short_drama.agent.sandbox.workspace import SandboxWorkspaceService, cleanup_run
from short_drama.agent.tools.langchain import create_langchain_tool
from short_drama.agent.tools.registry import get_spec
from short_drama.core.exceptions import BusinessError
from short_drama.service.ai_config.management.assistant_models import model_credentials
from short_drama.service.assistant.execution.graph import AgentGraphService
from short_drama.service.assistant.execution.run import AgentRunService
from short_drama.service.assistant.execution.scope import AgentExecutionScopeService
from short_drama.service.assistant.execution.waiting import GenerationWaitService

logger = logging.getLogger(__name__)


def recoverable_persistence_error(error: Exception) -> bool:
    # 仅连接/锁等暂态故障可继续同一检查点；SQL契约错误和普通代码异常重跑仍会失败。
    return isinstance(error, (LeaseRenewalUnavailable, PoolTimeoutError)) or (
        isinstance(error, DBAPIError)
        and (
            error.connection_invalidated
            or bool(error.orig.args)
            and error.orig.args[0] in {1040, 1205, 1213, 2002, 2003, 2006, 2013}
        )
    )


async def stream_run(
    graph, *, claim, context, config, snapshot, initial_messages, waits=None, events=None
):
    resume_values = waits.resume_values(claim, snapshot) if waits is not None else {}
    if snapshot is not None and snapshot.interrupts and not resume_values:
        return snapshot
    graph_input = (
        {"messages": initial_messages}
        if snapshot is None or not snapshot.next and claim.pending_input
        else Command(resume=resume_values)
        if resume_values
        else None
    )
    async for event in graph.astream(
        graph_input,
        config,
        context=context,
        stream_mode=["messages", "updates", "custom"],
        subgraphs=True,
        durability="sync",
    ):
        if events is not None:
            await events.accept(event, claim)
    saved = await graph.aget_state(config, subgraphs=True)
    if waits is not None:
        await waits.bind_snapshot(saved, claim)
    return saved


class AgentRuntime:
    def __init__(self, factory, settings, *, transport=None, resolver_factory=None):
        self.factory, self.settings, self.transport = factory, settings, transport
        self.resolver_factory = resolver_factory

    def _service(self, method, *args, **kwargs):
        with self.factory() as session:
            return getattr(AgentRunService(session, self.settings, tool_lookup=get_spec), method)(
                *args, **kwargs
            )

    def execute(self, run_id: int, message_version: int) -> None:
        claim = self._service("claim", int(run_id), int(message_version))
        if claim is None:
            return
        try:
            asyncio.run(self._execute(claim))
        except LeaseLost:
            # 停止仍持本租约；真正丢租约时 finish 不会覆盖新 Worker。
            self._service("finish", claim, status="cancelled")
        except ModelCallLimitExceededError:
            self._service(
                "finish", claim, status="failed", error={"code": "agent_budget_exhausted"}
            )
        except (AgentGatewayError, BusinessError) as error:
            facts = getattr(error, "context_budget", None)
            self._service(
                "finish",
                claim,
                status="failed",
                error={
                    "code": error.code,
                    **(
                        {"accepted_unknown": True}
                        if getattr(error, "accepted_unknown", False)
                        else {}
                    ),
                    **({"context_budget": facts} if facts is not None else {}),
                },
            )
        except Exception as error:
            if invalid_tool_selection(error):
                self._service(
                    "finish", claim, status="failed", error={"code": "invalid_agent_tool_selection"}
                )
                return
            if recoverable_persistence_error(error):
                logger.exception("agent_graph_interrupted", extra={"run_id": claim.run_id})
                raise RuntimeError("Agent graph interrupted; durable recovery required") from None
            logger.exception("agent_graph_failed", extra={"run_id": claim.run_id})
            self._service("finish", claim, status="failed", error={"code": "agent_execution_error"})
        finally:
            cleanup_run(self.factory, self.settings, claim.run_id)

    def _revision_input(self, claim):
        from short_drama.agent.core.access import lock_run
        from short_drama.agent.core.fence import owns_execution
        from short_drama.agent.task.discussion import prepare_revision

        with self.factory.begin() as session:
            _project, conversation, run = lock_run(session, claim.run_id)
            if not owns_execution(run, claim.fence):
                raise LeaseLost()
            return prepare_revision(session, conversation, run)

    async def _execute(self, claim):
        graph_service = AgentGraphService(
            self.factory,
            owner_id=claim.owner_id,
            task_id=claim.task_id,
            run_id=claim.run_id,
            version=ENGINE_VERSION,
            fence=claim.fence,
        )
        saver = MySQLCheckpointSaver(graph_service)
        context = RuntimeContext(
            owner_id=claim.owner_id,
            task_id=claim.task_id,
            run_id=claim.run_id,
            root_scope_id=claim.scope_id,
            fence=claim.fence,
            factory=self.factory,
            settings=self.settings,
            root_tool_lookup=get_spec,
            scopes=AgentExecutionScopeService(graph_service),
            root_tool_names=claim.tool_names,
            subtask_slots=asyncio.Semaphore(
                claim.snapshot["assistant_settings"]["subagent_max_concurrency"]
            ),
        )
        context = replace(
            context, waits=GenerationWaitService(context), questions=RuntimeQuestions(context)
        )
        context = replace(context, sandbox=SandboxWorkspaceService(context))
        events = AgentEventAdapter(context)
        context = replace(context, events=events)
        await asyncio.to_thread(events.recover)
        resources = GraphResources(context, claim.snapshot, self.resolver_factory)
        callbacks = RunModelLedger(context).callbacks(
            max_output_tokens=claim.budget["output_tokens_per_call"],
            hydrate=resources.hydrate,
            project_input=resources.project_input,
            input_scope=resources.input_scope,
        )
        model = build_chat_model(
            claim.snapshot,
            model_credentials(claim.snapshot, self.settings.encryption_key),
            callbacks=callbacks,
            transport=self.transport,
        )
        descriptions = claim.snapshot["assistant_settings"]["tool_descriptions"]
        tools = [
            create_langchain_tool(get_spec(name), description=descriptions[name])
            for name in claim.tool_names
            if name != "task"
        ]
        initial_messages = []
        system_prompt = claim.snapshot["assistant_settings"]["system_prompt"]
        if claim.discussion_revision:
            values = await asyncio.to_thread(self._revision_input, claim)
            prompt = values["user_prompt"]
            initial_messages = [
                HumanMessage(content=prompt["content"] if isinstance(prompt, dict) else prompt)
            ]
            system_prompt += "\n" + values["instructions"]
            tools = []
            middleware = [
                ExecutionMiddleware(resources),
                ModelCallLimitMiddleware(run_limit=1, exit_behavior="error"),
            ]
        else:
            middleware = [ExecutionMiddleware(resources)]
            delegation, selection_tools = None, tools
            if "task" in claim.tool_names:
                child_tools = [tool for tool in tools if tool.name not in CHILD_EXCLUDED_TOOLS]
                child_middleware = assistant_middleware(
                    model=model, tools=child_tools, snapshot=claim.snapshot
                )
                child_middleware[0] = ContextMiddleware(child=True)
                delegation = delegation_middleware(
                    model=model,
                    child_tools=child_tools,
                    child_middleware=[
                        ExecutionMiddleware(resources, child=True),
                        *child_middleware,
                    ],
                    backend=ScopedBackend(),
                    system_prompt=system_prompt,
                    task_description=descriptions["task"],
                )
                middleware.append(TaskScopeMiddleware())
                selection_tools = [*tools, *delegation.tools]
            middleware.extend(
                assistant_middleware(model=model, tools=selection_tools, snapshot=claim.snapshot)
            )
            if delegation is not None:
                middleware.append(delegation)
        graph = build_agent(
            model=model,
            tools=tools,
            middleware=middleware,
            checkpointer=saver,
            system_prompt=system_prompt,
        )
        # 每次实际模型请求均扣父 Run 账本；图步数仅容纳预算内所有中间件/工具节点。
        config = {**saver._config(), "recursion_limit": 16 * claim.budget["decision_calls"] + 32}

        async def deliver():
            snapshot = await graph.aget_state(config, subgraphs=True)
            if not snapshot.values:
                snapshot = None
            saved = (
                snapshot
                if snapshot is not None and not snapshot.next and not claim.pending_input
                else await stream_run(
                    graph,
                    claim=claim,
                    context=context,
                    config=config,
                    snapshot=snapshot,
                    initial_messages=initial_messages,
                    waits=context.waits,
                    events=events,
                )
            )
            if saved.interrupts:
                await asyncio.to_thread(
                    context.sandbox.release_for_wait, claim.run_id, fence=claim.fence
                )
                await asyncio.to_thread(self._service, "wait", claim)
                return
            message = saved.values["messages"][-1]
            if not isinstance(message, AIMessage) or message.tool_calls or not message.text.strip():
                raise AgentGatewayError("empty_agent_response")
            await asyncio.to_thread(
                self._service,
                "finish",
                claim,
                status="succeeded",
                content=message.text,
                model_call_id=message.response_metadata.get("protected_usage", {}).get(
                    "model_call_id"
                ),
            )

        try:

            async def renew():
                return await asyncio.to_thread(self._service, "renew", claim)

            async def control():
                return await asyncio.to_thread(self._service, "execution_active", claim)

            await run_with_lease(
                deliver(),
                renew=renew,
                interval=max(0.1, self.settings.agent_lease_seconds / 3),
                lease_until=claim.lease_until,
                control=control,
                sandbox=context.sandbox,
            )
        finally:
            context.sandbox.close()
            resources.close()
