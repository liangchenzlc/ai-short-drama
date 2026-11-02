"""One LangChain decision, using the existing protected HTTP admission boundary.

LangGraph owns tool execution. Neither SDK can retry or perform implicit reads.
Only references, never hydrated entities, reach a persistence callback.
"""

import asyncio
import base64
import json
import logging
import time
from functools import cached_property
from importlib.metadata import version
from typing import Any
from uuid import uuid4

import anthropic
import httpx2
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel, LangSmithParams
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    message_chunk_to_message,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from openai._streaming import SSEDecoder
from pydantic import Field

from short_drama.agent.gateway.contracts import (
    AgentCapabilityEvidence,
    AgentGatewayError,
    ModelCallbacks,
)
from short_drama.agent.gateway.deepseek import DeepSeekChatModel
from short_drama.agent.gateway.inputs import _credentials, select_protocol
from short_drama.agent.gateway.transport import (
    _ProtectedHTTPTransport,
    _ReplaySafeTransport,
    _TextRedactor,
    _usage_evidence,
)
from short_drama.agent.resources.contracts import assert_reference_payload
from short_drama.agent.resources.resolver import request_template
from short_drama.agent.runtime.trace_content import reasoning_texts
from short_drama.ai.gateway.endpoints import endpoint
from short_drama.ai.gateway.transport import SafeTransport
from short_drama.ai.types import GenerationError

FRAMEWORK_VERSION = f"langchain-core:{version('langchain-core')}"
REQUEST_CODEC = "agent.langchain-request"
logger = logging.getLogger(__name__)


def _endpoints(snapshot, protocol):
    if protocol == "claude_messages.v1":
        from short_drama.ai.gateway.transport import validated_url

        root = validated_url(snapshot["base_url"]).rstrip("/")
        if root.endswith("/v1/messages"):
            root = root.removesuffix("/messages")
        elif not root.endswith("/v1"):
            root += "/v1"
        return root + "/messages", root.removesuffix("/v1")
    suffix = "/responses" if protocol == "openai_responses.v1" else "/chat/completions"
    return endpoint(snapshot["base_url"], suffix, protocol), endpoint(
        snapshot["base_url"], "", protocol
    )


class _ProtectedAnthropic(ChatAnthropic):
    # LangChain currently exposes no public HTTP client injection for Anthropic.
    # Keep this single SDK adaptation covered by the native tool-result test.
    protected_client: object = Field(exclude=True)

    @cached_property
    def _async_client(self):
        return self.protected_client


class _HarnessHTTPTransport(_ProtectedHTTPTransport):
    def __init__(self, *args, resources=(), tool_results=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.resources = list(resources)
        self.tool_results = tool_results

    async def checkpoint(self, callback, payload, code):
        if code == "agent_request_checkpoint_failed":
            payload = {
                **payload,
                "codec": REQUEST_CODEC,
                "framework_version": FRAMEWORK_VERSION,
                "body": request_template(payload["body"], self.resources, self.tool_results),
            }
            assert_reference_payload(payload)
        elif code == "agent_response_checkpoint_failed":
            # Text models have no reason to return media bodies. Do not let an
            # echoed request smuggle image bytes through the opaque raw receipt.
            content = base64.b64decode(payload["body_b64"])
            if not content and payload["status_code"] >= 300:
                # SafeTransport deliberately omits server error bodies. Preserve
                # the HTTP failure instead of parsing absent content as JSON.
                await super().checkpoint(callback, payload, code)
                return
            try:
                if payload["content_type"].startswith("text/event-stream"):
                    decoded = [
                        event.json()
                        for event in SSEDecoder().iter_bytes(iter([content]))
                        if event.data and event.data != "[DONE]"
                    ]
                else:
                    decoded = json.loads(content)
                assert_reference_payload(decoded)

                def strings(value):
                    if isinstance(value, str):
                        return value
                    if isinstance(value, dict):
                        return "".join(strings(child) for child in value.values())
                    if isinstance(value, list):
                        return "".join(strings(child) for child in value)
                    return ""

                combined = strings(decoded)
                assert_reference_payload(combined)
                if any(item.content in combined for item in self.resources):
                    raise AgentGatewayError("agent_media_entity_leak", accepted_unknown=True)
            except (ValueError, TypeError):
                raise AgentGatewayError(
                    "incomplete_agent_response", accepted_unknown=True
                ) from None
        await super().checkpoint(callback, payload, code)


class _HarnessReplay(_ReplaySafeTransport):
    def __init__(self, request, raw, resources, limit, tool_results=None):
        if (
            request.get("codec") != REQUEST_CODEC
            or request.get("framework_version") != FRAMEWORK_VERSION
            or raw.get("codec") != "agent.raw-response"
            or raw.get("version") != 1
        ):
            raise AgentGatewayError("invalid_agent_replay")
        self.expected, self.raw, self.resources = request, raw, resources
        self.tool_results = tool_results
        try:
            self.content = base64.b64decode(raw["body_b64"], validate=True)
        except (ValueError, KeyError):
            raise AgentGatewayError("invalid_agent_replay") from None
        if len(self.content) > limit:
            raise AgentGatewayError("invalid_agent_replay")

    def request(self, method, url, *, body, **kwargs):
        if request_template(body, self.resources, self.tool_results) != self.expected["body"]:
            raise AgentGatewayError("agent_replay_request_mismatch")
        return super().request(method, url, body=self.expected["body"], **kwargs)


class ProtectedChatModel(BaseChatModel):
    """Each framework invocation owns a fresh protected HTTP admission and receipt."""

    snapshot: dict = Field(exclude=True, repr=False)
    credential: object = Field(exclude=True, repr=False)
    model_callbacks: ModelCallbacks = Field(exclude=True, repr=False)
    transport: object = Field(exclude=True, repr=False)

    @property
    def _llm_type(self) -> str:
        return "workbench-protected-chat"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> LangSmithParams:
        params = super()._get_ls_params(stop=stop, **kwargs)
        protocol = select_protocol(self.snapshot)
        params["ls_provider"] = (
            "anthropic"
            if protocol == "claude_messages.v1"
            else "deepseek"
            if protocol == "openai_chat.v1" and self.snapshot.get("provider") == "deepseek"
            else "openai"
        )
        return params

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("Use the asynchronous protected model API")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        callbacks = self.model_callbacks
        protocol = select_protocol(self.snapshot)
        url, root = _endpoints(self.snapshot, protocol)
        secret, headers, secrets = _credentials(self.credential)
        resources, tool_results = list(callbacks.resources), callbacks.tool_results
        if callbacks.hydrate is not None:
            messages, resources, tool_results = await callbacks.hydrate(messages)
        call_id = uuid4().hex
        source = (getattr(run_manager, "metadata", None) or {}).get("lc_source", "assistant")

        async def before_send(payload):
            if callbacks.before_send is not None:
                try:
                    await callbacks.before_send(
                        {**payload, "model_call_id": call_id, "source": source}
                    )
                except AgentGatewayError as error:
                    if error.context_budget is not None:
                        error.context_budget.update(
                            source=source,
                            phase={"summarization": "helper", "tool_selection": "selector"}.get(
                                source, "main"
                            ),
                        )
                    raise

        async def save_raw(payload):
            if callbacks.save_raw is not None:
                safe_payload = dict(payload)
                content = base64.b64decode(payload["body_b64"]).decode("utf-8")
                content = _TextRedactor(secrets).add(content, final=True)
                safe_payload["body_b64"] = base64.b64encode(content.encode()).decode("ascii")
                await callbacks.save_raw(
                    {**safe_payload, "model_call_id": call_id, "source": source}
                )

        maximum = getattr(callbacks.settings, "generation_max_response_bytes", 8 * 1024**2)
        replay = callbacks.raw is not None
        safe = (
            _HarnessReplay(callbacks.request, callbacks.raw, resources, maximum, tool_results)
            if replay
            else self.transport
        )
        bridge = _HarnessHTTPTransport(
            safe,
            url=url,
            protocol=protocol,
            secret=secret,
            credential_headers=headers,
            deadline=time.monotonic() + callbacks.timeout_seconds,
            max_response_bytes=maximum,
            on_request=None if replay else before_send,
            on_response=None if replay else save_raw,
            resources=resources,
            tool_results=tool_results,
        )
        redactor = _TextRedactor(secrets)
        reasoning_redactor = _TextRedactor(secrets)

        async def visible_content(channel, text, *, final=False):
            cleaner = reasoning_redactor if channel == "reasoning" else redactor
            visible = cleaner.add(text, final=final)
            if visible and callbacks.on_model_content and not replay:
                await callbacks.on_model_content(
                    {
                        "model_call_id": call_id,
                        "source": source,
                        "channel": channel,
                        "delta": visible,
                    }
                )
            if visible and channel == "text" and callbacks.on_text_delta and not replay:
                await callbacks.on_text_delta(visible)

        model_timeout = asyncio.timeout(callbacks.timeout_seconds)
        try:
            async with (
                model_timeout,
                httpx2.AsyncClient(
                    transport=bridge,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=callbacks.timeout_seconds,
                ) as client,
            ):
                if protocol == "claude_messages.v1":
                    provider = anthropic.AsyncAnthropic(
                        api_key=secret or "not-configured",
                        base_url=root,
                        http_client=client,
                        max_retries=0,
                        timeout=callbacks.timeout_seconds,
                    )
                    model = _ProtectedAnthropic(
                        model=self.snapshot["model_key"],
                        api_key=secret or "not-configured",
                        base_url=root,
                        max_tokens=callbacks.max_output_tokens,
                        max_retries=0,
                        protected_client=provider,
                        timeout=callbacks.timeout_seconds,
                    )
                else:
                    provider = AsyncOpenAI(
                        api_key=secret or "not-configured",
                        base_url=root,
                        http_client=client,
                        max_retries=0,
                        timeout=callbacks.timeout_seconds,
                    )
                    model_class = (
                        DeepSeekChatModel
                        if protocol == "openai_chat.v1"
                        and self.snapshot.get("provider") == "deepseek"
                        else ChatOpenAI
                    )
                    model = model_class(
                        model=self.snapshot["model_key"],
                        api_key=secret or "not-configured",
                        base_url=root,
                        root_async_client=provider,
                        async_client=provider.chat.completions,
                        http_async_client=client,
                        max_retries=0,
                        max_tokens=callbacks.max_output_tokens,
                        use_responses_api=protocol == "openai_responses.v1",
                        use_previous_response_id=False,
                        store=False,
                        stream_usage=True,
                    )
                if (
                    protocol == "openai_chat.v1"
                    and self.snapshot.get("provider") == "deepseek"
                    and source == "summarization"
                ):
                    kwargs["extra_body"] = {
                        **kwargs.get("extra_body", {}),
                        "thinking": {"type": "disabled"},
                    }
                tools = kwargs.pop("tools", ())
                runnable = model.bind_tools(tools, **kwargs) if tools else model.bind(**kwargs)
                if callbacks.stream:
                    aggregate = None
                    async for chunk in runnable.astream(messages, stop=stop):
                        aggregate = chunk if aggregate is None else aggregate + chunk
                        if not replay:
                            for reasoning in reasoning_texts(chunk):
                                await visible_content("reasoning", reasoning)
                            if chunk.text:
                                await visible_content("text", chunk.text)
                    if aggregate is None:
                        raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
                    result = message_chunk_to_message(aggregate)
                    if not replay:
                        await visible_content("text", "", final=True)
                        await visible_content("reasoning", "", final=True)
                else:
                    result = await runnable.ainvoke(messages, stop=stop)
                    if not replay:
                        for reasoning in reasoning_texts(result):
                            await visible_content("reasoning", reasoning)
                        await visible_content("reasoning", "", final=True)
                        await visible_content("text", result.text, final=True)
            if not isinstance(result, AIMessage) or bridge.raw_response is None:
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            evidence, terminal = _usage_evidence(bridge.raw_response, protocol)
            if protocol == "claude_messages.v1":
                content = base64.b64decode(bridge.raw_response["body_b64"])
                terminal = not callbacks.stream or any(
                    event.json().get("type") == "message_stop"
                    for event in SSEDecoder().iter_bytes(iter([content]))
                    if event.data
                )
                evidence = {
                    "usage_reported": result.usage_metadata is not None,
                    "output_tokens_reported": result.usage_metadata is not None,
                }
            usage = {
                **(result.usage_metadata or {}),
                **evidence,
                "external_requests": bridge.requests,
                "model_call_id": call_id,
                "source": source,
            }
            if "cached_tokens" not in usage:
                cached = (usage.get("input_token_details") or {}).get("cache_read")
                if type(cached) is int and cached >= 0:
                    usage["cached_tokens"] = cached
            # 可用性校验不能撤销供应商已经受理并报告的用量事实。
            if callbacks.usage is not None and not replay:
                await callbacks.usage(usage)
            finish = result.response_metadata.get("finish_reason") or result.response_metadata.get(
                "stop_reason"
            )
            if protocol == "openai_responses.v1":
                finish = result.response_metadata.get("status")
            if finish in {"length", "max_tokens", "incomplete"}:
                raise AgentGatewayError("agent_response_truncated")
            if (
                finish
                not in {
                    "stop",
                    "tool_calls",
                    "end_turn",
                    "tool_use",
                    "stop_sequence",
                    "completed",
                }
                or callbacks.stream
                and not terminal
            ):
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            if result.invalid_tool_calls:
                raise AgentGatewayError("invalid_agent_tool_calls")
            # Only the transient provider request contains hydrated media or credentials.
            values = result.model_dump()

            def redact(value):
                if isinstance(value, str):
                    return _TextRedactor(secrets).add(value, final=True)
                if isinstance(value, list):
                    return [redact(item) for item in value]
                if isinstance(value, dict):
                    return {key: redact(item) for key, item in value.items()}
                return value

            result = AIMessage.model_validate(redact(values))
            assert_reference_payload(result.model_dump())
            result.response_metadata.update(
                {
                    "protected_usage": usage,
                    "protocol": protocol,
                    "requests": bridge.requests,
                    "streaming": callbacks.stream,
                }
            )
            if callbacks.on_model_content is not None and not replay:
                await callbacks.on_model_content(
                    {
                        "model_call_id": call_id,
                        "source": source,
                        "channel": "end",
                        "status": "succeeded",
                    }
                )
            return ChatResult(generations=[ChatGeneration(message=result)])
        except asyncio.CancelledError:
            if callbacks.on_model_content is not None and not replay:
                await callbacks.on_model_content(
                    {
                        "model_call_id": call_id,
                        "source": source,
                        "channel": "end",
                        "status": "interrupted",
                    }
                )
            if callbacks.error is not None and not replay:
                await callbacks.error(
                    {
                        "model_call_id": call_id,
                        "source": source,
                        "code": "agent_model_cancelled",
                        "requests": bridge.requests,
                        "accepted_unknown": bool(bridge.requests),
                        "not_accepted": not bridge.requests,
                    }
                )
            raise
        except Exception as error:
            cause = bridge.last_error
            if isinstance(error, AgentGatewayError):
                failure = error
            elif isinstance(cause, GenerationError):
                failure = AgentGatewayError(
                    cause.code,
                    requests=bridge.requests,
                    accepted_unknown=cause.accepted_unknown,
                    http_status=cause.http_status,
                    context_budget=getattr(cause, "context_budget", None),
                )
            elif model_timeout.expired() and isinstance(error, TimeoutError):
                failure = AgentGatewayError(
                    "timeout", requests=bridge.requests, accepted_unknown=bool(bridge.requests)
                )
            else:
                logger.warning("agent_gateway_sdk_failure (%s)", type(error).__name__)
                failure = AgentGatewayError(
                    "agent_provider_error",
                    requests=bridge.requests,
                    accepted_unknown=bool(bridge.requests),
                )
            failure.requests = bridge.requests
            if callbacks.error is not None and not replay:
                status = (bridge.raw_response or {}).get("status_code", 0)
                await callbacks.error(
                    {
                        "model_call_id": call_id,
                        "source": source,
                        "code": failure.code,
                        **(
                            {"context_budget": failure.context_budget}
                            if failure.context_budget is not None
                            else {}
                        ),
                        "requests": bridge.requests,
                        "accepted_unknown": failure.accepted_unknown,
                        "not_accepted": not bridge.requests
                        or 300 <= status < 500
                        and status != 408,
                    }
                )
            if callbacks.on_model_content is not None and not replay:
                await callbacks.on_model_content(
                    {
                        "model_call_id": call_id,
                        "source": source,
                        "channel": "end",
                        "status": "unknown" if failure.accepted_unknown else "failed",
                    }
                )
            raise failure from None


def build_chat_model(
    snapshot, credential, *, callbacks: ModelCallbacks, transport=None
) -> BaseChatModel:
    select_protocol(snapshot)
    _credentials(credential)
    return ProtectedChatModel(
        snapshot=snapshot,
        credential=credential,
        model_callbacks=callbacks,
        transport=transport or SafeTransport(callbacks.settings),
    )


class LangChainGateway:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport or SafeTransport(settings)

    async def validate_capability(self, snapshot, credential, *, conversation_id, on_request=None):
        from langchain.agents.middleware import ModelCallLimitMiddleware
        from langchain_core.tools import StructuredTool

        from short_drama.agent.runtime.agent import build_agent

        calls = []

        def echo(value: str) -> str:
            calls.append(value)
            return value

        tool = StructuredTool.from_function(
            echo,
            name="verify_agent_echo",
            description="Return the fixed check value agent-capability-v1",
        )
        requests = 0

        async def admit(payload):
            nonlocal requests
            if on_request is not None:
                await on_request(payload)
            requests += 1

        model = build_chat_model(
            snapshot,
            credential,
            callbacks=ModelCallbacks(
                settings=self.settings,
                before_send=admit,
                max_output_tokens=256,
            ),
            transport=self.transport,
        )
        graph = build_agent(
            model=model,
            tools=[tool],
            middleware=[ModelCallLimitMiddleware(run_limit=2)],
            checkpointer=None,
            system_prompt="Call verify_agent_echo once with value agent-capability-v1. "
            "After its result reply exactly AGENT_CAPABILITY_OK.",
        )
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="Run the capability check.")]}
        )
        result = state["messages"][-1]
        return AgentCapabilityEvidence(
            select_protocol(snapshot),
            calls == ["agent-capability-v1"],
            bool(calls) and result.text.strip() == "AGENT_CAPABILITY_OK",
            streaming="verified",
            requests=requests,
            framework_version=FRAMEWORK_VERSION,
        )
