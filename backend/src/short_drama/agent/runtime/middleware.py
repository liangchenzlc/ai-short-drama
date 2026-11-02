"""Framework context, source-aware summaries and per-call Skill tool dependencies."""

import asyncio
import json
from contextlib import nullcontext

from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware, hook_config
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command

from short_drama.agent.context.budget import estimate_input_tokens, input_limit, wire_tool_counts
from short_drama.agent.context.capabilities import context_profile
from short_drama.agent.context.graph import scope_context
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.runtime.context import execution_scope, model_compaction
from short_drama.agent.runtime.summarization import BoundedSummary
from short_drama.core.exceptions import Conflict


class ExecutionMiddleware(AgentMiddleware):
    def __init__(self, resources, *, child=False):
        self.resources = resources
        self.child = child

    def _check_scope(self):
        if self.child and execution_scope.get() is None:
            raise Conflict("子图执行缺少已持久关联的执行范围")

    async def awrap_model_call(self, request, handler):
        from short_drama.service.assistant.execution.tools import AgentToolExecutionService

        self._check_scope()
        service = AgentToolExecutionService(request.runtime.context)
        info = request.runtime.execution_info
        identity = {
            "thread_id": info.thread_id,
            "checkpoint_id": info.checkpoint_id,
            "checkpoint_ns": info.checkpoint_ns,
            "task_id": info.task_id,
        }
        signature, saved = await asyncio.to_thread(service.model_input, request.state, identity)
        if saved is not None:
            return saved
        permitted = set(request.state.get("permitted_tool_names", []))
        token = self.resources.model_state.set(request.state)
        compaction_token = model_compaction.set([])
        try:
            response = await handler(
                request.override(tools=[tool for tool in request.tools if tool.name in permitted])
            )
            message = response if isinstance(response, AIMessage) else response.result[-1]
            await asyncio.to_thread(service.model_result, signature, message)
            return response
        finally:
            self.resources.model_state.reset(token)
            model_compaction.reset(compaction_token)

    async def awrap_tool_call(self, request, handler):
        from short_drama.agent.core.access import LeaseLost
        from short_drama.core.exceptions import BusinessError
        from short_drama.service.assistant.execution.tools import AgentToolExecutionService

        self._check_scope()
        service = AgentToolExecutionService(request.runtime.context)
        operation_id, saved = await asyncio.to_thread(
            service.start_tool, request.tool_call, request.state
        )
        if saved is not None:
            return saved
        if request.runtime.context.events is not None:
            await asyncio.to_thread(request.runtime.context.events.operation_content, operation_id)
        try:
            from short_drama.agent.sandbox.backend import sandbox_call

            token = sandbox_call.set(
                (request.runtime.context, request.tool_call["id"], request.tool_call["name"])
            )
            try:
                output = await handler(request)
            finally:
                sandbox_call.reset(token)
        except LeaseLost:
            raise
        except BusinessError as error:
            output = ToolMessage(
                content=json.dumps(
                    {
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "details": getattr(error, "details", {}),
                        }
                    },
                    ensure_ascii=False,
                ),
                name=request.tool_call["name"],
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        output = await asyncio.to_thread(self.resources.prepare_result, operation_id, output)
        if request.runtime.context.events is not None:
            message = (
                next(item for item in output.update["messages"] if isinstance(item, ToolMessage))
                if hasattr(output, "update")
                else output
            )
            await asyncio.to_thread(
                request.runtime.context.events.operation_content,
                operation_id,
                result=message.content,
            )
        return await asyncio.to_thread(service.finish_tool, operation_id, output)


class TaskScopeMiddleware(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] != "task":
            return await handler(request)
        context = request.runtime.context
        scope_id = await asyncio.to_thread(
            context.scopes.for_delegation,
            context.run_id,
            context.scope_id,
            request.tool_call["id"],
            fence=context.fence,
            memory_epoch=request.state.get("memory_epoch", 0),
        )
        async with context.subtask_slots:
            token = execution_scope.set(scope_id)
            try:
                return await handler(request)
            finally:
                execution_scope.reset(token)


class ContextMiddleware(AgentMiddleware):
    def __init__(self, *, child=False):
        self.child = child

    async def awrap_model_call(self, request, handler):
        # 同ID更新保留旧位置；selector须在摘要前看到末尾的规范目标Human。
        return await handler(
            request.override(
                messages=[
                    *[
                        message
                        for message in request.messages
                        if not message.additional_kwargs.get("task_context_pinned")
                    ],
                    *[
                        message
                        for message in request.messages
                        if message.additional_kwargs.get("task_context_pinned")
                    ],
                ]
            )
        )

    async def abefore_model(self, state, runtime):
        if self.child and execution_scope.get() is None:
            raise Conflict("子图执行缺少已持久关联的执行范围")
        # ExecutionInfo 的末段属于当前 middleware 节点，前缀才是图的持久命名空间。
        namespace = runtime.execution_info.checkpoint_ns.rpartition("|")[0]
        return await asyncio.to_thread(scope_context, runtime.context, state, namespace)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        from short_drama.service.assistant.execution.tools import AgentToolExecutionService

        message = state["messages"][-1]
        if isinstance(message, AIMessage) and not message.tool_calls:
            if await asyncio.to_thread(
                AgentToolExecutionService(runtime.context).pending_input, state
            ):
                return {"jump_to": "model"}
        return None


def _pinned_pairs(messages, activations):
    operations = {
        item["body_operation_id"]
        for item in activations
        if item.get("active") and item.get("body_operation_id")
    }
    call_ids = set()
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == "runtime__load_skill":
            result = json.loads(message.content)
            if result.get("instructions") and result.get("body_operation_id") in operations:
                call_ids.add(message.tool_call_id)
    group_ids = set()
    for message in messages:
        if isinstance(message, AIMessage) and any(
            call["id"] in call_ids for call in message.tool_calls
        ):
            group_ids.add(message.id)
            call_ids.update(call["id"] for call in message.tool_calls)
    paired = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
    if not call_ids.issubset(paired):
        raise Conflict("活动方法的工具调用组不完整")
    return [
        message
        for message in messages
        if message.id in group_ids
        or (isinstance(message, ToolMessage) and message.tool_call_id in call_ids)
    ]


class SkillSummarizationMiddleware(AgentMiddleware):
    def __init__(self, *, model, trigger=None, keep=None, trim_tokens_to_summarize=None):
        self.model, self.trigger, self.keep = model, trigger, keep
        self.trim_tokens_to_summarize = trim_tokens_to_summarize

    async def awrap_model_call(self, request, handler):
        callbacks = getattr(request.model, "model_callbacks", None)
        scope = getattr(callbacks, "input_scope", None)
        with scope(request.messages) if scope is not None else nullcontext():
            return await self._summarize(request, handler)

    async def _summarize(self, request, handler):
        state, messages = request.state, request.messages
        context_messages = [
            message for message in messages if message.additional_kwargs.get("task_context_pinned")
        ]
        pinned = [
            *_pinned_pairs(messages, state.get("skill_activations", [])),
            *context_messages,
        ]
        pinned_ids = {message.id for message in pinned}
        unpinned = [message for message in messages if message.id not in pinned_ids]
        snapshot = getattr(request.model, "snapshot", {})
        callbacks = getattr(request.model, "model_callbacks", None)
        output = getattr(callbacks, "max_output_tokens", 8192)
        window = context_profile(snapshot)["context_window_tokens"]
        limit = input_limit(output, context_window=window)
        envelope = {
            "model": snapshot.get("model_key", ""),
            "tools": [convert_to_openai_tool(tool) for tool in request.tools],
            "max_tokens": output,
        }
        project = getattr(callbacks, "project_input", None) or convert_to_openai_messages

        original_ids = {message.id for message in messages}

        def retained_messages(history):
            history = list(history)
            kept = {message.id for message in history if not isinstance(message, RemoveMessage)}
            return [
                *[
                    message
                    for message in history
                    if not isinstance(message, RemoveMessage) and message.id not in original_ids
                ],
                *[
                    message
                    for message in messages
                    if message.id in kept | pinned_ids
                    and not message.additional_kwargs.get("task_context_pinned")
                ],
                *context_messages,
            ]

        def count(history):
            retained = [
                *([request.system_message] if request.system_message else []),
                *retained_messages(history),
            ]
            return estimate_input_tokens(
                {
                    **envelope,
                    "messages": project(retained),
                },
                references=True,
            )

        fixed = count([])

        def reject(phase, estimated):
            return AgentGatewayError(
                "agent_context_budget_exhausted",
                context_budget={
                    "phase": phase,
                    "source": "assistant",
                    "context_window_tokens": window,
                    "reserved_output_tokens": output,
                    "input_limit_tokens": limit,
                    "fixed_estimated_input_tokens": fixed,
                    "fixed_kind": "system_tools_and_pinned",
                    "estimated_input_tokens": estimated,
                    "message_count": len(messages),
                    **wire_tool_counts(convert_to_openai_messages(messages)),
                },
            )

        if fixed >= limit:
            raise reject("fixed", fixed)
        keep = self.keep or ("tokens", fixed + int((limit - fixed) * 0.25))
        summary_input = unpinned
        last_group = next(
            (
                index
                for index in range(len(unpinned) - 1, -1, -1)
                if isinstance(unpinned[index], AIMessage)
            ),
            max(0, len(unpinned) - 1),
        )
        boundary = next(
            (
                message
                for message in reversed(context_messages)
                if isinstance(message, HumanMessage)
            ),
            None,
        )
        retention_limit = keep[1] if keep[0] == "tokens" else limit
        if boundary is not None and count(unpinned[last_group:]) > retention_limit:
            projected = {"messages": project(unpinned[last_group:])}
            # 当前待交付像素须保留真实组；仅纯文本大组可由已有目标Human划公开边界。
            if estimate_input_tokens(projected, references=True) == estimate_input_tokens(
                projected
            ):
                summary_input = [*unpinned, boundary]
                keep = ("messages", 1)
        helper = BoundedSummary(self.model)
        # Native retention counts the same fixed envelope; reserve its cost in both thresholds.
        summarizer = SummarizationMiddleware(
            model=self.model,
            token_counter=count,
            trigger=self.trigger or ("tokens", fixed + int((limit - fixed) * 0.7)),
            keep=keep,
            trim_tokens_to_summarize=self.trim_tokens_to_summarize,
            summarizer=helper.summarize,
        )
        result = await summarizer.abefore_model(
            {**state, "messages": summary_input}, request.runtime
        )
        if result is None:
            if count(unpinned) > limit:
                raise reject("post_summary", count(unpinned))
            return await handler(request)
        kept = {
            message.id for message in result["messages"] if not isinstance(message, RemoveMessage)
        }
        sources, activations, subjects = set(), set(), set()
        for message in unpinned:
            if message.id not in kept:
                sources.update(message.additional_kwargs.get("source_message_ids", []))
                activations.update(message.additional_kwargs.get("skill_activation_ids", []))
                subjects.update(message.additional_kwargs.get("subject_keys", []))
        for message in result["messages"]:
            if message.additional_kwargs.get("lc_source") == "summarization":
                message.additional_kwargs.update(
                    source_message_ids=sorted(sources),
                    skill_activation_ids=sorted(activations),
                    subject_keys=sorted(subjects),
                    summary_helper_calls=helper.calls,
                )
        # 真实调用组保持原顺序；内部上下文放末尾，使目标Human仍是selector输入。
        compacted = [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *retained_messages(result["messages"]),
        ]
        retained = [message for message in compacted if not isinstance(message, RemoveMessage)]
        if count([message for message in retained if message.id not in pinned_ids]) > limit:
            raise reject(
                "post_summary",
                count([message for message in retained if message.id not in pinned_ids]),
            )
        response = await handler(
            request.override(messages=retained, state={**state, "messages": retained})
        )
        # Framework composition unwraps inner Commands before Execution sees the response.
        pending = model_compaction.get()
        if pending is not None:
            pending.extend(retained)
        if isinstance(response, AIMessage):
            response = ModelResponse(result=[response])
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"messages": [*compacted, *response.result]}),
        )


class SkillToolsMiddleware(AgentMiddleware):
    def __init__(self, tools):
        self.available_tools = {tool.name: tool for tool in tools}

    async def awrap_model_call(self, request, handler):
        context = request.runtime.context
        permitted = set(request.state.get("permitted_tool_names", []))
        required = {
            name
            for item in request.state.get("skill_activations", [])
            if item.get("active")
            for name in item["skill"].get("required_tools", [])
        }
        chosen = {tool.name for tool in request.tools}
        extra = []
        for name in sorted(required - chosen):
            spec = context.tool_lookup(name)
            if spec is None or name not in permitted or name not in self.available_tools:
                raise Conflict("当前范围缺少方法所需工具：" + name)
            extra.append(self.available_tools[name])
        return await handler(request.override(tools=[*request.tools, *extra]))
