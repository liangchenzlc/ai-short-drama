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

import anthropic
import httpx2
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from openai._streaming import SSEDecoder
from pydantic import Field

from short_drama.agent.gateway.contracts import AgentCapabilityEvidence, AgentGatewayError
from short_drama.agent.gateway.inputs import _credentials
from short_drama.agent.gateway.transport import (
    _ProtectedHTTPTransport,
    _ReplaySafeTransport,
    _TextRedactor,
    _usage_evidence,
)
from short_drama.agent.resources.contracts import assert_reference_payload
from short_drama.agent.resources.resolver import HydratedResource, request_template
from short_drama.ai.adapters.catalog import select_adapter
from short_drama.ai.gateway.endpoints import endpoint
from short_drama.ai.gateway.transport import SafeTransport
from short_drama.ai.types import GenerationError

FRAMEWORK_VERSION = f"langchain-core:{version('langchain-core')}"
REQUEST_CODEC = "agent.langchain-request"
logger = logging.getLogger(__name__)


def select_protocol(snapshot: dict) -> str:
    profile = snapshot.get("runtime_profile") or {}
    if profile:
        protocol = {
            ("openai", "chat-completion"): "openai_chat.v1",
            ("openai", "openai-response"): "openai_responses.v1",
            ("claude", "claude-messages"): "claude_messages.v1",
            ("claude", "messages"): "claude_messages.v1",
        }.get((profile.get("api_format"), profile.get("protocol")))
    else:
        protocol = select_adapter(snapshot)
    if snapshot.get("service_type") != "text" or protocol not in {
        "openai_chat.v1",
        "openai_responses.v1",
        "claude_messages.v1",
    }:
        raise AgentGatewayError("unsupported_agent_protocol")
    return protocol


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


class LangChainGateway:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport or SafeTransport(settings)

    async def validate_capability(self, snapshot, credential, *, conversation_id, on_request=None):
        """The model management probe uses exactly the same nodes and transport."""
        tool = {
            "name": "verify_agent_echo",
            "description": "Return the fixed check value",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string", "enum": ["agent-capability-v1"]}},
                "required": ["value"],
                "additionalProperties": False,
            },
        }
        messages = [
            SystemMessage(
                content="Call verify_agent_echo once with value agent-capability-v1. "
                "After its result reply exactly AGENT_CAPABILITY_OK."
            ),
            HumanMessage(content="Run the capability check."),
        ]
        kwargs = dict(
            tools=[tool],
            resources=[],
            max_output_tokens=256,
            max_tool_calls=1,
            on_request=on_request,
        )
        first = await self.decide(snapshot, credential, messages=messages, **kwargs)
        calls = first["output"].get("calls", []) if first["output_kind"] == "tool_requests" else []
        valid = (
            len(calls) == 1
            and calls[0]["tool_name"] == tool["name"]
            and calls[0]["args"] == {"value": "agent-capability-v1"}
        )
        if not valid:
            return AgentCapabilityEvidence(
                first["protocol"],
                False,
                False,
                requests=first["requests"],
                framework_version=FRAMEWORK_VERSION,
            )
        call = calls[0]
        messages += [
            AIMessage(
                content=first["narration"],
                tool_calls=[
                    {"name": call["tool_name"], "args": call["args"], "id": call["tool_call_id"]}
                ],
            ),
            ToolMessage(content="agent-capability-v1", tool_call_id=call["tool_call_id"]),
        ]
        try:
            second = await self.decide(snapshot, credential, messages=messages, **kwargs)
        except AgentGatewayError as error:
            error.requests += first["requests"]
            raise
        return AgentCapabilityEvidence(
            first["protocol"],
            True,
            second["output_kind"] == "text" and second["output"].strip() == "AGENT_CAPABILITY_OK",
            streaming="verified",
            requests=first["requests"] + second["requests"],
            framework_version=FRAMEWORK_VERSION,
        )

    async def decide(
        self,
        snapshot: dict,
        credential,
        *,
        messages: list[BaseMessage],
        tools: list[dict],
        resources: list[HydratedResource],
        tool_results: dict | None = None,
        max_output_tokens: int,
        max_tool_calls: int = 16,
        timeout_seconds: float = 120,
        stream: bool = True,
        on_request=None,
        on_response=None,
        on_text_delta=None,
        request=None,
        raw=None,
    ) -> dict:
        protocol = select_protocol(snapshot)
        url, root = _endpoints(snapshot, protocol)
        secret, headers, secrets = _credentials(credential)
        maximum = getattr(self.settings, "generation_max_response_bytes", 8 * 1024**2)
        replay = raw is not None
        safe = (
            _HarnessReplay(request, raw, resources, maximum, tool_results)
            if replay
            else self.transport
        )
        bridge = _HarnessHTTPTransport(
            safe,
            url=url,
            protocol=protocol,
            secret=secret,
            credential_headers=headers,
            deadline=time.monotonic() + timeout_seconds,
            max_response_bytes=maximum,
            on_request=None if replay else on_request,
            on_response=None if replay else on_response,
            resources=resources,
            tool_results=tool_results,
        )
        redactor = _TextRedactor(secrets)
        try:
            async with (
                asyncio.timeout(timeout_seconds),
                httpx2.AsyncClient(
                    transport=bridge,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=timeout_seconds,
                ) as client,
            ):
                if protocol == "claude_messages.v1":
                    provider = anthropic.AsyncAnthropic(
                        api_key=secret or "not-configured",
                        base_url=root,
                        http_client=client,
                        max_retries=0,
                        timeout=timeout_seconds,
                    )
                    model = _ProtectedAnthropic(
                        model=snapshot["model_key"],
                        api_key=secret or "not-configured",
                        base_url=root,
                        max_tokens=max_output_tokens,
                        max_retries=0,
                        protected_client=provider,
                        timeout=timeout_seconds,
                    )
                else:
                    provider = AsyncOpenAI(
                        api_key=secret or "not-configured",
                        base_url=root,
                        http_client=client,
                        max_retries=0,
                        timeout=timeout_seconds,
                    )
                    model = ChatOpenAI(
                        model=snapshot["model_key"],
                        api_key=secret or "not-configured",
                        base_url=root,
                        root_async_client=provider,
                        async_client=provider.chat.completions,
                        http_async_client=client,
                        max_retries=0,
                        max_tokens=max_output_tokens,
                        use_responses_api=protocol == "openai_responses.v1",
                        use_previous_response_id=False,
                        store=False,
                        stream_usage=True,
                    )
                runnable = model.bind_tools(tools) if tools and max_tool_calls else model
                if stream:
                    aggregate = None
                    async for chunk in runnable.astream(messages):
                        aggregate = chunk if aggregate is None else aggregate + chunk
                        if on_text_delta and not replay and chunk.text:
                            visible = redactor.add(chunk.text)
                            if visible:
                                await on_text_delta(visible)
                    if aggregate is None:
                        raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
                    result = message_chunk_to_message(aggregate)
                    if on_text_delta and not replay:
                        visible = redactor.add("", final=True)
                        if visible:
                            await on_text_delta(visible)
                else:
                    result = await runnable.ainvoke(messages)
            if not isinstance(result, AIMessage) or bridge.raw_response is None:
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            finish = result.response_metadata.get("finish_reason") or result.response_metadata.get(
                "stop_reason"
            )
            if protocol == "openai_responses.v1":
                finish = result.response_metadata.get("status")
            if finish in {"length", "max_tokens", "incomplete"}:
                raise AgentGatewayError("agent_response_truncated")
            if finish not in {
                "stop",
                "tool_calls",
                "end_turn",
                "tool_use",
                "stop_sequence",
                "completed",
            }:
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            if result.invalid_tool_calls:
                raise AgentGatewayError("invalid_agent_tool_calls")
            calls = [
                {"tool_name": item["name"], "args": item["args"], "tool_call_id": item["id"]}
                for item in result.tool_calls
            ]
            if (
                len(calls) > max_tool_calls
                or len({item["tool_call_id"] for item in calls}) != len(calls)
                or any(
                    not item["tool_call_id"]
                    or len(item["tool_call_id"]) > 255
                    or item["tool_name"] not in {tool["name"] for tool in tools}
                    for item in calls
                )
            ):
                raise AgentGatewayError("invalid_agent_tool_calls")
            text = _TextRedactor(secrets).add(result.text, final=True)
            if not text.strip() and not calls:
                raise AgentGatewayError("empty_agent_response", accepted_unknown=True)
            evidence, terminal = _usage_evidence(bridge.raw_response, protocol)
            if protocol == "claude_messages.v1":
                content = base64.b64decode(bridge.raw_response["body_b64"])
                terminal = not stream or any(
                    event.json().get("type") == "message_stop"
                    for event in SSEDecoder().iter_bytes(iter([content]))
                    if event.data
                )
                evidence = {
                    "usage_reported": result.usage_metadata is not None,
                    "output_tokens_reported": result.usage_metadata is not None,
                }
            if stream and not terminal:
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            usage = {
                **(result.usage_metadata or {}),
                **evidence,
                "external_requests": bridge.requests,
            }
            if "cached_tokens" not in usage:
                cached = (usage.get("input_token_details") or {}).get("cache_read")
                if type(cached) is int and cached >= 0:
                    usage["cached_tokens"] = cached
            normalized = {
                "output_kind": "tool_requests" if calls else "text",
                "output": {"calls": calls, "approvals": []} if calls else text,
                "narration": text if calls else "",
                "usage": usage,
                "protocol": protocol,
                "requests": bridge.requests,
                "streaming": stream,
            }
            assert_reference_payload(normalized)
            return normalized
        except AgentGatewayError:
            raise
        except Exception as error:
            cause = bridge.last_error
            if isinstance(cause, GenerationError):
                raise AgentGatewayError(
                    cause.code,
                    requests=bridge.requests,
                    accepted_unknown=cause.accepted_unknown,
                    http_status=cause.http_status,
                ) from None
            logger.warning(
                "agent_gateway_sdk_failure (%s)",
                type(error).__name__,
                extra={
                    "protocol": protocol,
                    "exception_type": type(error).__name__,
                    "transport_error_type": type(cause).__name__ if cause is not None else None,
                    "requests": bridge.requests,
                    "response_received": bridge.response_received,
                },
            )
            raise AgentGatewayError(
                "agent_provider_error",
                requests=bridge.requests,
                accepted_unknown=bool(bridge.requests),
            ) from error
