"""段落执行及能力探测编排；业务工具必须在持久化后单独执行。"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import httpx2
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.external import ExternalToolset
from pydantic_ai.usage import UsageLimits

from short_drama.agent.gateway.contracts import FRAMEWORK_VERSION as FRAMEWORK_VERSION
from short_drama.agent.gateway.contracts import HISTORY_CODEC_VERSION as HISTORY_CODEC_VERSION
from short_drama.agent.gateway.contracts import MAX_HISTORY_BYTES as MAX_HISTORY_BYTES
from short_drama.agent.gateway.contracts import MAX_REQUEST_BYTES as MAX_REQUEST_BYTES
from short_drama.agent.gateway.contracts import (
    MAX_TOOLS,
    AgentCapabilityEvidence,
    AgentGatewayError,
    AgentSegmentResult,
)
from short_drama.agent.gateway.contracts import provider_failure_code as provider_failure_code
from short_drama.agent.gateway.history import (
    _DEFERRED_ADAPTER,
    _json_value,
    deserialize_deferred_results,
    deserialize_history,
    serialize_deferred_requests,
    serialize_history,
)
from short_drama.agent.gateway.history import _RESULTS_ADAPTER as _RESULTS_ADAPTER
from short_drama.agent.gateway.history import serialize_segment_result as serialize_segment_result
from short_drama.agent.gateway.inputs import _BINARY_ADAPTER as _BINARY_ADAPTER
from short_drama.agent.gateway.inputs import _credential as _credential
from short_drama.agent.gateway.inputs import _credentials, _prompt_content, select_agent_protocol
from short_drama.agent.gateway.transport import _CapturedStream as _CapturedStream
from short_drama.agent.gateway.transport import (
    _narration_text,
    _ProtectedHTTPTransport,
    _ReplaySafeTransport,
    _TextRedactor,
    _usage_evidence,
)
from short_drama.agent.gateway.transport import _raw_response as _raw_response
from short_drama.ai.gateway.endpoints import endpoint
from short_drama.ai.gateway.transport import SafeTransport
from short_drama.ai.types import GenerationError


class AgentModelGateway:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport or SafeTransport(settings)

    async def run_segment(
        self,
        snapshot,
        credential,
        *,
        instructions,
        user_prompt=None,
        history=None,
        tools: Sequence[ToolDefinition] = (),
        deferred_results: DeferredToolResults | Mapping[str, Any] | None = None,
        conversation_id=None,
        max_output_tokens=8192,
        max_tool_calls=16,
        timeout_seconds=120,
        stream=False,
        on_request: Callable[[dict], Awaitable[None]] | None = None,
        on_response: Callable[[dict], Awaitable[None]] | None = None,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentSegmentResult:
        """Return a completed decision or deferred intents; never execute a domain tool."""
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("service_type") != "text"
            or not isinstance(snapshot.get("model_key"), str)
            or not snapshot["model_key"].strip()
            or not isinstance(snapshot.get("base_url"), str)
            or not isinstance(instructions, str)
            or not instructions.strip()
            or (user_prompt is not None and not isinstance(user_prompt, (str, list)))
            or type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= 8192
            or type(max_tool_calls) is not int
            or not 0 <= max_tool_calls <= MAX_TOOLS
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 120
            or type(stream) is not bool
            or (on_text_delta is not None and not stream)
        ):
            raise AgentGatewayError("invalid_agent_segment")
        secret, credential_headers, secrets = _credentials(credential)
        user_prompt = _prompt_content(user_prompt, snapshot)
        messages = deserialize_history(history)
        # Inline bytes are durable protocol data. URL downloads would bypass the
        # protected POST and offline replay and therefore remain forbidden.
        permitted_parts = {
            "system-prompt",
            "user-prompt",
            "tool-return",
            "retry-prompt",
            "text",
            "thinking",
            "tool-call",
        }
        for message in messages:
            for part in message.parts:
                if part.part_kind not in permitted_parts:
                    raise AgentGatewayError("unsupported_agent_history_content")
                if part.part_kind == "user-prompt":
                    _prompt_content(part.content, snapshot)
                if part.part_kind == "tool-return":
                    _json_value(part.content)
        if not messages and user_prompt is None:
            raise AgentGatewayError("empty_agent_input")
        if any(not isinstance(tool, ToolDefinition) for tool in tools):
            raise AgentGatewayError("invalid_agent_tool_manifest")
        if len(tools) > MAX_TOOLS or len({tool.name for tool in tools}) != len(tools):
            raise AgentGatewayError("invalid_agent_tool_manifest")
        if any(tool.kind != "function" or not tool.name for tool in tools):
            raise AgentGatewayError("invalid_agent_tool_manifest")
        if isinstance(deferred_results, Mapping):
            deferred_results = deserialize_deferred_results(deferred_results)
        if deferred_results is not None:
            for tool_result in deferred_results.calls.values():
                _json_value(tool_result)
        try:
            protocol = select_agent_protocol(snapshot)
            url = endpoint(snapshot["base_url"], "/responses", protocol)
            if protocol == "openai_chat.v1":
                url = endpoint(snapshot["base_url"], "/chat/completions", protocol)
            base_url = endpoint(snapshot["base_url"], "", protocol).rstrip("/") + "/"
        except AgentGatewayError:
            raise
        except (GenerationError, KeyError, TypeError):
            raise AgentGatewayError("invalid_agent_configuration") from None
        bridge = _ProtectedHTTPTransport(
            self.transport,
            url=url,
            protocol=protocol,
            secret=secret,
            credential_headers=credential_headers,
            deadline=time.monotonic() + timeout_seconds,
            max_response_bytes=getattr(self.settings, "generation_max_response_bytes", 8 * 1024**2),
            on_request=on_request,
            on_response=on_response,
        )
        redactor = _TextRedactor(secrets)

        async def text_events(context, events):
            async for event in events:
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    text = event.part.content
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    text = event.delta.content_delta
                else:
                    continue
                visible = redactor.add(text)
                if visible:
                    await bridge.checkpoint(on_text_delta, visible, "agent_event_checkpoint_failed")

        try:
            async with (
                asyncio.timeout(timeout_seconds),
                httpx2.AsyncClient(
                    transport=bridge,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=timeout_seconds,
                ) as http_client,
            ):
                client = AsyncOpenAI(
                    api_key=secret or "not-configured",
                    base_url=base_url,
                    http_client=http_client,
                    max_retries=0,
                    timeout=timeout_seconds,
                )
                provider = OpenAIProvider(openai_client=client)
                model_type = (
                    OpenAIChatModel if protocol == "openai_chat.v1" else OpenAIResponsesModel
                )
                agent = Agent(
                    model_type(snapshot["model_key"], provider=provider),
                    instructions=instructions,
                    output_type=[str, DeferredToolRequests],
                    toolsets=[ExternalToolset(list(tools))] if tools else [],
                    retries=0,
                    model_settings={"max_tokens": max_output_tokens, "openai_store": False},
                )
                result = await agent.run(
                    user_prompt,
                    message_history=messages,
                    deferred_tool_results=deferred_results,
                    conversation_id=conversation_id,
                    usage_limits=UsageLimits(request_limit=1, tool_calls_limit=max_tool_calls),
                    infer_name=False,
                    event_stream_handler=text_events if stream else None,
                )
            all_messages = result.all_messages()
            responses = [
                message for message in result.new_messages() if isinstance(message, ModelResponse)
            ]
            if not responses:
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            terminal = responses[-1]
            if bridge.raw_response is None:
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            usage_evidence, raw_terminal = _usage_evidence(bridge.raw_response, protocol)
            if stream and not raw_terminal:
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            if terminal.finish_reason == "length":
                raise AgentGatewayError("agent_response_truncated")
            if terminal.finish_reason in {"content_filter", "error"}:
                raise AgentGatewayError("agent_response_rejected")
            if (
                terminal.state != "complete"
                or terminal.finish_reason not in {"stop", "tool_call"}
                or (
                    protocol == "openai_chat.v1"
                    and not (terminal.provider_details or {}).get("finish_reason")
                )
            ):
                raise AgentGatewayError("incomplete_agent_response", accepted_unknown=True)
            normalized_history = serialize_history(all_messages)
            if isinstance(result.output, DeferredToolRequests):
                requests = serialize_deferred_requests(result.output)
                if requests["approvals"] or len(requests["calls"]) > max_tool_calls:
                    raise AgentGatewayError("invalid_agent_tool_calls")
                output = _DEFERRED_ADAPTER.validate_python(requests)
                call_ids = [call.tool_call_id for call in output.calls]
                if (
                    len(set(call_ids)) != len(call_ids)
                    or any(not identifier or len(identifier) > 255 for identifier in call_ids)
                    or any(
                        call.tool_name not in {tool.name for tool in tools} for call in output.calls
                    )
                ):
                    raise AgentGatewayError("invalid_agent_tool_calls")
            else:
                if not isinstance(result.output, str) or not result.output.strip():
                    raise AgentGatewayError("empty_agent_response", accepted_unknown=True)
                output = _TextRedactor(secrets).add(result.output, final=True)
            final_visible = redactor.add("", final=True)
            if final_visible:
                await bridge.checkpoint(
                    on_text_delta, final_visible, "agent_event_checkpoint_failed"
                )
            usage = TypeAdapter(type(result.usage)).dump_python(result.usage, mode="json")
            usage.update(usage_evidence, external_requests=bridge.requests)
            narration = (
                _narration_text(terminal, secrets)
                if isinstance(output, DeferredToolRequests)
                else ""
            )
            return AgentSegmentResult(
                output=output,
                history=normalized_history,
                usage=_json_value(usage),
                protocol=protocol,
                requests=bridge.requests,
                streaming=stream
                and bridge.raw_response["content_type"].split(";", 1)[0].strip().lower()
                == "text/event-stream",
                narration=narration,
            )
        except AgentGatewayError as error:
            raise AgentGatewayError(
                error.code,
                requests=bridge.requests,
                accepted_unknown=error.accepted_unknown,
                http_status=error.http_status,
            ) from None
        except (ModelHTTPError, APIStatusError) as error:
            status = error.status_code
            raise AgentGatewayError(
                getattr(bridge.last_error, "code", "agent_provider_rejected")
                if status < 500
                else "agent_provider_unknown",
                requests=bridge.requests,
                accepted_unknown=status >= 500 or status == 408,
                http_status=status,
            ) from None
        except (APIConnectionError, GenerationError) as error:
            source = bridge.last_error or error
            raise AgentGatewayError(
                getattr(source, "code", "agent_transport_error"),
                requests=bridge.requests,
                accepted_unknown=getattr(source, "accepted_unknown", bool(bridge.requests)),
            ) from None
        except (
            ModelAPIError,
            UnexpectedModelBehavior,
            UsageLimitExceeded,
            ValidationError,
            ValueError,
            TypeError,
            APIError,
            TimeoutError,
        ):
            source = bridge.last_error
            raise AgentGatewayError(
                getattr(source, "code", "invalid_agent_response"),
                requests=bridge.requests,
                accepted_unknown=getattr(source, "accepted_unknown", bool(bridge.requests)),
            ) from None

    async def replay_segment(
        self,
        snapshot,
        credential="",
        *,
        request_payload,
        raw_response,
        **segment_input,
    ) -> AgentSegmentResult:
        """Parse a saved response with the real SDK, checking its frozen request locally."""
        if any(
            segment_input.get(name) is not None
            for name in ("on_request", "on_response", "on_text_delta")
        ):
            raise AgentGatewayError("invalid_agent_replay")
        replay = _ReplaySafeTransport(
            request_payload,
            raw_response,
            getattr(self.settings, "generation_max_response_bytes", 8 * 1024**2),
        )
        boundary = AgentModelGateway(self.settings, transport=replay)
        return await boundary.run_segment(snapshot, credential, **segment_input)

    async def validate_capability(
        self,
        snapshot,
        credential,
        *,
        conversation_id,
        on_segment_result: Callable[[AgentSegmentResult], Awaitable[None]] | None = None,
        stream=True,
        on_request: Callable[[dict], Awaitable[None]] | None = None,
        on_response: Callable[[dict], Awaitable[None]] | None = None,
    ) -> AgentCapabilityEvidence:
        """Explicit two-request probe. Admission, budget and user consent belong to the caller."""
        probe = ToolDefinition(
            name="verify_agent_echo",
            description="Return the fixed capability-check value without changing project data.",
            parameters_json_schema={
                "type": "object",
                "properties": {"value": {"type": "string", "enum": ["agent-capability-v1"]}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )
        instructions = (
            "This is a capability check. Call verify_agent_echo once with value "
            "agent-capability-v1. After receiving its result, reply exactly AGENT_CAPABILITY_OK."
        )
        first = await self.run_segment(
            snapshot,
            credential,
            instructions=instructions,
            user_prompt="Run the capability check.",
            tools=[probe],
            conversation_id=conversation_id,
            max_output_tokens=256,
            max_tool_calls=1,
            stream=stream,
            on_request=on_request,
            on_response=on_response,
        )
        if on_segment_result is not None:
            await on_segment_result(first)
        requests = first.output
        if not isinstance(requests, DeferredToolRequests) or len(requests.calls) != 1:
            return AgentCapabilityEvidence(
                first.protocol, False, False, requests=first.requests, segments=(first,)
            )
        call = requests.calls[0]
        if call.tool_name != probe.name or call.args_as_dict() != {"value": "agent-capability-v1"}:
            return AgentCapabilityEvidence(
                first.protocol, False, False, requests=first.requests, segments=(first,)
            )
        try:
            second = await self.run_segment(
                snapshot,
                credential,
                instructions=instructions,
                history=first.history,
                tools=[probe],
                deferred_results=DeferredToolResults(
                    calls={call.tool_call_id: "agent-capability-v1"}
                ),
                conversation_id=conversation_id,
                max_output_tokens=256,
                max_tool_calls=1,
                stream=stream,
                on_request=on_request,
                on_response=on_response,
            )
        except AgentGatewayError as error:
            error.requests += first.requests
            error.completed_segments = (first,)
            raise
        if on_segment_result is not None:
            await on_segment_result(second)
        return AgentCapabilityEvidence(
            first.protocol,
            True,
            isinstance(second.output, str) and second.output.strip() == "AGENT_CAPABILITY_OK",
            requests=first.requests + second.requests,
            segments=(first, second),
            streaming="verified" if first.streaming and second.streaming else "not_tested",
        )
