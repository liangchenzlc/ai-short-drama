"""Checkpointed length continuation, independent of cumulative token/call meters."""

import asyncio
import json

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from short_drama.agent.context.capabilities import request_output_tokens
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.runtime.model_ledger import RunModelLedger
from short_drama.utils.json_digest import digest


def recovery_update(state, snapshot):
    message = state["messages"][-1]
    revision, epoch = state.get("applied_input_revision", 0), state.get("memory_epoch", 0)
    saved = state.get("output_recovery") or {}
    recovery = (
        dict(saved)
        if saved.get("input_revision", 0) == revision and saved.get("memory_epoch", 0) == epoch
        else {}
    )
    recovery.update(input_revision=revision, memory_epoch=epoch)
    recovery.setdefault("fragments", [])
    info = (
        message.response_metadata.get("output_recovery") if isinstance(message, AIMessage) else None
    )
    if info is None:
        recovery.pop("next_output_tokens", None)
        recovery.pop("tool_attempts", None)
        return {"output_recovery": recovery}
    call_id, kind = info["model_call_id"], info["kind"]
    if recovery.get("last_call_id") != call_id:
        if kind == "reasoning":
            current = info["max_output_tokens"]
            increased = request_output_tokens(snapshot, "assistant", current * 2)
            if increased <= current:
                raise AgentGatewayError("agent_reasoning_output_limit")
            recovery["next_output_tokens"] = increased
        elif kind == "tool_arguments":
            recovery["tool_attempts"] = recovery.get("tool_attempts", 0) + 1
        else:
            recovery["tool_attempts"] = 0
        if message.text.strip():
            fingerprint = digest(message.text)
            repeated = (
                recovery.get("repeated_text", 0) + 1
                if recovery.get("text_hash") == fingerprint
                else 0
            )
            if repeated >= 2:
                raise AgentGatewayError("agent_output_no_progress")
            if not repeated:
                recovery["fragments"] = [*recovery["fragments"], call_id]
            recovery.update(text_hash=fingerprint, repeated_text=repeated)
            if not repeated:
                recovery["tool_attempts"] = 0
        if recovery.get("tool_attempts", 0) >= 3:
            raise AgentGatewayError("agent_output_no_progress")
        recovery["last_call_id"] = call_id
    instructions = (
        "上一轮工具参数达到输出长度限制，残缺调用未执行。请拆成更小的完整工具调用，不要重复大参数。"
        if kind == "tool_arguments"
        else "上一轮达到输出长度限制。请继续完成当前任务；只输出后续内容，不重复已经输出的正文。"
    )
    return {
        "output_recovery": recovery,
        "messages": [HumanMessage(content=instructions, id=f"output-continue:{call_id}")],
        "jump_to": "model",
    }


def tool_failure_update(state):
    results = []
    for message in reversed(state["messages"]):
        if message.additional_kwargs.get("task_context_pinned"):
            continue
        if not isinstance(message, ToolMessage):
            break
        results.append(message)
    previous = state.get("tool_failure_streak") or {}
    if not results:
        return {"tool_failure_streak": {}}
    calls = {
        call["id"]: call
        for message in state["messages"]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    errors = []
    for message in results:
        try:
            error = json.loads(message.content).get("error")
        except (ValueError, TypeError, AttributeError):
            error = None
        if message.status != "error" and not error:
            return {"tool_failure_streak": {}}
        error = error or message.content
        errors.append([message.name or "", calls[message.tool_call_id]["args"], error])
    fingerprint = digest(sorted(errors, key=digest))
    group = sorted(message.tool_call_id for message in results)
    count = previous.get("count", 0) if previous.get("fingerprint") == fingerprint else 0
    if previous.get("group") != group:
        count += 1
    if count >= 3:
        raise AgentGatewayError("agent_tool_no_progress")
    return {"tool_failure_streak": {"fingerprint": fingerprint, "count": count, "group": group}}


class OutputRecoveryMiddleware(AgentMiddleware):
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def abefore_model(self, state, runtime):
        if not state.get("output_recovery"):
            await asyncio.to_thread(RunModelLedger(runtime.context).recovery_state, None)
        return tool_failure_update(state)

    async def awrap_model_call(self, request, handler):
        output = (request.state.get("output_recovery") or {}).get("next_output_tokens")
        if output is not None:
            request = request.override(
                model_settings={**request.model_settings, "agent_output_tokens": output}
            )
        return await handler(request)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        update = recovery_update(state, self.snapshot)
        ledger = RunModelLedger(runtime.context)
        if update.get("jump_to"):
            await asyncio.to_thread(
                ledger.recovery_state,
                "output_continuation",
                update["output_recovery"]["last_call_id"],
            )
        else:
            await asyncio.to_thread(ledger.recovery_state, None)
            if (
                runtime.context.scope_id != runtime.context.root_scope_id
                and update["output_recovery"].get("fragments")
                and not state["messages"][-1].tool_calls
            ):
                from short_drama.service.assistant.execution.tools import AgentToolExecutionService

                message = state["messages"][-1]
                content = await asyncio.to_thread(
                    AgentToolExecutionService(runtime.context).model_output,
                    {**state, **update},
                    message,
                )
                update["messages"] = [message.model_copy(update={"content": content})]
        return update
