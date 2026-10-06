"""One protected model request per segment, with every business tool deferred.

This module has no database or domain-service dependencies. Callers must admit and
persist decisions privately before executing any returned tool intent.

History and tool intents are private protocol state, not display payloads. Keep
their identifiers, arguments and text intact for continuation; redact credentials
only from the separately returned user-visible text. Never log or expose the
private state directly.
"""

import asyncio
import base64
import json
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any, Literal

import httpx2
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI
from openai._streaming import SSEDecoder
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelMessagesTypeAdapter,
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

from short_drama.ai.adapters import endpoint, select_adapter
from short_drama.ai.canvas_credentials import CanvasCredentials
from short_drama.ai.transport import SafeTransport
from short_drama.ai.types import GenerationError
from short_drama.schemas.model_runtime_profile import ModelRuntimeProfile

FRAMEWORK_VERSION = version("pydantic-ai-slim")
HISTORY_CODEC_VERSION = 1
MAX_HISTORY_BYTES = 8 * 1024**2
MAX_REQUEST_BYTES = 8 * 1024**2
MAX_TOOLS = 32
_DEFERRED_ADAPTER = TypeAdapter(DeferredToolRequests)
_RESULTS_ADAPTER = TypeAdapter(DeferredToolResults)
_BINARY_ADAPTER = TypeAdapter(BinaryContent)


def select_agent_protocol(snapshot):
    profile = snapshot.get("runtime_profile")
    if profile is None:
        protocol = select_adapter(snapshot)
    else:
        try:
            profile = ModelRuntimeProfile.model_validate(profile)
        except (ValidationError, TypeError):
            raise AgentGatewayError("unsupported_agent_protocol") from None
        protocol = {
            "chat-completion": "openai_chat.v1",
            "openai-response": "openai_responses.v1",
        }.get(profile.protocol)
        if profile.api_format != "openai":
            raise AgentGatewayError("unsupported_agent_protocol")
    if snapshot.get("service_type") != "text" or protocol not in {
        "openai_chat.v1",
        "openai_responses.v1",
    }:
        raise AgentGatewayError("unsupported_agent_protocol")
    return protocol


def _prompt_content(value, snapshot):
    """Only inline managed bytes are accepted; SDK URL objects remain forbidden."""
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, list) or not value or len(value) > 80:
        raise AgentGatewayError("unsupported_agent_history_content")
    try:
        protocol = select_agent_protocol(snapshot)
    except (GenerationError, KeyError, TypeError):
        raise AgentGatewayError("unsupported_agent_protocol") from None
    parts = []
    total = 0
    for item in value:
        if isinstance(item, str):
            total += len(item.encode("utf-8"))
            parts.append(item)
            continue
        if isinstance(item, dict):
            if item.keys() - {"kind", "data", "media_type", "identifier", "vendor_metadata"}:
                raise AgentGatewayError("unsupported_agent_history_content")
            try:
                item = _BINARY_ADAPTER.validate_python(item)
            except ValidationError:
                raise AgentGatewayError("unsupported_agent_history_content") from None
        if not isinstance(item, BinaryContent):
            raise AgentGatewayError("unsupported_agent_history_content")
        if item.media_type in {"image/jpeg", "image/png", "image/webp"}:
            supported = True
        elif item.media_type in {"audio/wav", "audio/mpeg"}:
            supported = protocol == "openai_chat.v1"
        else:
            supported = False
        if not supported or not item.data:
            raise AgentGatewayError("unsupported_agent_input_modality")
        total += len(item.data)
        parts.append(item)
    if total > 4 * 1024**2 + 262144:
        raise AgentGatewayError("agent_request_too_large")
    return parts


class AgentGatewayError(GenerationError):
    """A safe error whose acceptance flag never authorizes automatic resubmission."""

    def __init__(self, code, *, requests=0, accepted_unknown=False, http_status=None):
        self.requests = requests
        self.completed_segments = ()
        super().__init__(code, accepted_unknown=accepted_unknown, http_status=http_status)


def provider_failure_code(status, content):
    if status in {401, 403}:
        return "agent_provider_authentication"
    if status == 429:
        return "agent_provider_rate_limited"
    if status >= 500 or status == 408:
        return "agent_provider_unknown"
    try:
        payload = json.loads(content[:65536])
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        details = " ".join(str(error.get(key, "")) for key in ("code", "type", "message")).lower()
    except (ValueError, TypeError, AttributeError):
        details = ""
    unsupported = any(word in details for word in ("not support", "unsupported", "not allowed"))
    if unsupported:
        if any(word in details for word in ("tool", "function_call")):
            return "agent_tools_unsupported"
        if any(word in details for word in ("audio", "input_audio")):
            return "agent_audio_input_unsupported"
        if any(word in details for word in ("image", "vision")):
            return "agent_image_input_unsupported"
    return "agent_provider_rejected"


def _json_value(value):
    """Normalize only finite, JSON-compatible data, without reflecting bad values."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_HISTORY_BYTES:
            raise ValueError
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError):
        raise AgentGatewayError("invalid_agent_history") from None


def serialize_history(messages: Sequence[ModelMessage]) -> dict:
    try:
        payload = ModelMessagesTypeAdapter.dump_python(messages, mode="json", warnings="error")
        return _json_value(
            {
                "codec": "pydantic-ai.messages",
                "version": HISTORY_CODEC_VERSION,
                "framework_version": FRAMEWORK_VERSION,
                "messages": payload,
            }
        )
    except (ValueError, TypeError):
        raise AgentGatewayError("invalid_agent_history") from None


def deserialize_history(payload: Mapping[str, Any] | None) -> list[ModelMessage]:
    if payload is None:
        return []
    normalized = _json_value(payload)
    if (
        not isinstance(normalized, dict)
        or normalized.get("codec") != "pydantic-ai.messages"
        or normalized.get("version") != HISTORY_CODEC_VERSION
        or normalized.get("framework_version") != FRAMEWORK_VERSION
        or not isinstance(normalized.get("messages"), list)
    ):
        raise AgentGatewayError("unsupported_agent_history")
    try:
        return ModelMessagesTypeAdapter.validate_python(normalized["messages"])
    except (ValueError, TypeError, ValidationError):
        raise AgentGatewayError("invalid_agent_history") from None


def serialize_deferred_requests(requests: DeferredToolRequests) -> dict:
    return _json_value(_DEFERRED_ADAPTER.dump_python(requests, mode="json", warnings="error"))


def deserialize_deferred_results(payload: Mapping[str, Any]) -> DeferredToolResults:
    try:
        return _RESULTS_ADAPTER.validate_python(_json_value(payload))
    except (ValueError, TypeError, ValidationError):
        raise AgentGatewayError("invalid_deferred_results") from None


def _credential(value):
    secret = value.get_secret_value() if hasattr(value, "get_secret_value") else value or ""
    if (
        not isinstance(secret, str)
        or len(secret) > 16384
        or any(ord(char) < 33 or ord(char) > 126 for char in secret)
    ):
        raise AgentGatewayError("invalid_credential")
    return secret


def _credentials(value):
    if not isinstance(value, CanvasCredentials):
        secret = _credential(value)
        return secret, {}, [secret] if secret else []
    try:
        value = CanvasCredentials.model_validate(value.model_dump())
    except (ValueError, ValidationError):
        raise AgentGatewayError("invalid_credential") from None
    secret = _credential(value.api_key)
    headers = {}
    secrets = [secret] if secret else []
    for item in value.headers:
        raw = item.value.get_secret_value()
        headers[item.name.lower()] = raw if raw.isascii() else raw.encode("utf-8")
        if raw:
            secrets.append(raw)
    return secret, headers, sorted(set(secrets), key=len, reverse=True)


@dataclass(frozen=True, repr=False)
class AgentSegmentResult:
    output: str | DeferredToolRequests
    history: dict
    usage: dict
    protocol: str
    requests: int
    streaming: bool = False


def serialize_segment_result(result: AgentSegmentResult) -> dict:
    """Private normalized layer; tools/history must never be projected as public text."""
    deferred = isinstance(result.output, DeferredToolRequests)
    return {
        "output_kind": "tool_requests" if deferred else "text",
        "output": serialize_deferred_requests(result.output) if deferred else result.output,
        "history": result.history,
        "usage": result.usage,
        "protocol": result.protocol,
        "requests": result.requests,
        "streaming": result.streaming,
    }


@dataclass(frozen=True)
class AgentCapabilityEvidence:
    protocol: str
    tool_calling: bool
    tool_result_continuation: bool
    streaming: Literal["verified", "not_tested"] = "not_tested"
    requests: int = 0
    framework_version: str = FRAMEWORK_VERSION
    segments: tuple[AgentSegmentResult, ...] = field(default=(), repr=False)


def _raw_response(status, headers, content):
    content_type = next(
        (value for key, value in headers.items() if key.lower() == "content-type"),
        "application/json",
    )
    return {
        "codec": "agent.raw-response",
        "version": 1,
        "status_code": status,
        "content_type": content_type,
        "body_b64": base64.b64encode(content).decode("ascii"),
    }


def _usage_evidence(raw, protocol):
    """Audit actual provider usage; framework zero defaults are not reported zeroes."""
    usage = None
    terminal = False
    chat_finished = False
    try:
        content = base64.b64decode(raw["body_b64"], validate=True)
        if raw["content_type"].split(";", 1)[0].strip().lower() == "text/event-stream":
            for event in SSEDecoder().iter_bytes(iter([content])):
                if event.data == "[DONE]":
                    terminal = True
                    continue
                data = event.json()
                if not isinstance(data, dict):
                    continue
                if protocol == "openai_chat.v1":
                    choices = data.get("choices")
                    if isinstance(choices, list) and any(
                        isinstance(choice, dict) and choice.get("finish_reason")
                        for choice in choices
                    ):
                        chat_finished = True
                    if chat_finished and isinstance(data.get("usage"), dict):
                        usage = data["usage"]
                elif data.get("type") == "response.completed":
                    terminal = True
                    completed = data.get("response")
                    usage = completed.get("usage") if isinstance(completed, dict) else None
        else:
            data = json.loads(content)
            usage = data.get("usage") if isinstance(data, dict) else None
            terminal = True
    except (ValueError, TypeError, KeyError, UnicodeError):
        pass
    field_name = "completion_tokens" if protocol == "openai_chat.v1" else "output_tokens"
    return {
        "usage_reported": isinstance(usage, dict),
        "output_tokens_reported": isinstance(usage, dict)
        and type(usage.get(field_name)) is int
        and usage[field_name] >= 0,
    }, terminal


class _ReplaySafeTransport:
    """Saved responses only. No networking dependency or live transport is retained."""

    def __init__(self, request_payload, raw_response, max_bytes):
        if (
            not isinstance(request_payload, dict)
            or request_payload.get("codec") != "agent.request"
            or request_payload.get("version") != 1
            or request_payload.get("framework_version") != FRAMEWORK_VERSION
            or request_payload.get("protocol") not in {"openai_chat.v1", "openai_responses.v1"}
            or not isinstance(request_payload.get("url"), str)
            or not isinstance(request_payload.get("body"), dict)
            or not isinstance(raw_response, dict)
            or raw_response.get("codec") != "agent.raw-response"
            or raw_response.get("version") != 1
            or type(raw_response.get("status_code")) is not int
            or not 100 <= raw_response["status_code"] <= 599
            or not isinstance(raw_response.get("content_type"), str)
            or not isinstance(raw_response.get("body_b64"), str)
            or len(raw_response["body_b64"]) > 4 * ((max_bytes + 2) // 3)
        ):
            raise AgentGatewayError("invalid_agent_replay")
        try:
            self.content = base64.b64decode(raw_response["body_b64"], validate=True)
        except ValueError:
            raise AgentGatewayError("invalid_agent_replay") from None
        if len(self.content) > max_bytes:
            raise AgentGatewayError("invalid_agent_replay")
        self.expected = _json_value(request_payload)
        self.raw = raw_response

    def request(self, method, url, *, body, on_headers=None, on_chunk=None, on_send=None, **kwargs):
        if method != "POST" or url != self.expected["url"] or body != self.expected["body"]:
            raise AgentGatewayError("agent_replay_request_mismatch")
        if on_send:
            on_send()
        headers = {"Content-Type": self.raw["content_type"]}
        status = self.raw["status_code"]
        if on_headers:
            on_headers(status, headers)
        if on_chunk:
            for offset in range(0, len(self.content), 65536):
                on_chunk(self.content[offset : offset + 65536])
        return status, headers, self.content


class _CapturedStream(httpx2.AsyncByteStream):
    def __init__(self, bridge):
        self.bridge = bridge

    async def __aiter__(self):
        while not self.bridge.ended:
            kind, value = await self.bridge.queue.get()
            if kind == "chunk":
                yield value
            else:
                self.bridge.ended = True
                if kind == "error":
                    raise value

    async def aclose(self):
        # SDKs close at their terminal event. Drain the bounded body and commit raw
        # before letting that close (and hence the completed decision) succeed.
        async for _ in self:
            pass
        if self.bridge.feed_task is not None:
            await self.bridge.feed_task


class _TextRedactor:
    def __init__(self, secrets):
        self.secrets = tuple(sorted(set(filter(None, secrets)), key=len, reverse=True))
        self.pending = ""

    def add(self, text, *, final=False):
        self.pending += text
        if not self.secrets:
            ready, self.pending = self.pending, ""
            return ready
        ready, index = [], 0
        while index < len(self.pending):
            remaining = self.pending[index:]
            if not final and any(secret.startswith(remaining) for secret in self.secrets):
                break
            matched = next(
                (secret for secret in self.secrets if remaining.startswith(secret)), None
            )
            if matched:
                ready.append("[redacted]")
                index += len(matched)
            else:
                ready.append(self.pending[index])
                index += 1
        self.pending = self.pending[index:]
        return "".join(ready)


class _ProtectedHTTPTransport(httpx2.AsyncBaseTransport):
    """Use the existing DNS-pinned transport; SDK retries cannot make a second POST."""

    def __init__(
        self,
        safe,
        *,
        url,
        protocol,
        secret,
        credential_headers,
        deadline,
        max_response_bytes,
        on_request,
        on_response,
    ):
        self.safe = safe
        self.url = url
        self.secret = secret
        self.credential_headers = credential_headers
        self.deadline = deadline
        self.max_response_bytes = max_response_bytes
        self.protocol = protocol
        self.on_request = on_request
        self.on_response = on_response
        self.attempts = 0
        self.requests = 0
        self.last_error = None
        self.response_received = False
        self.raw_response = None
        self.queue = None
        self.feed_task = None
        self.ended = False
        self.stop = threading.Event()

    async def checkpoint(self, callback, payload, code):
        if callback is None:
            return
        try:
            await callback(payload)
        except Exception:
            self.last_error = AgentGatewayError(
                code, requests=self.requests, accepted_unknown=bool(self.requests)
            )
            raise self.last_error from None

    async def save_response(self, status, headers, content):
        self.response_received = True
        self.raw_response = _raw_response(status, headers, content)
        await self.checkpoint(
            self.on_response, self.raw_response, "agent_response_checkpoint_failed"
        )
        if 300 <= status < 400:
            self.last_error = AgentGatewayError(
                "agent_provider_redirect", requests=self.requests, http_status=status
            )
            raise self.last_error
        if status >= 400:
            self.last_error = AgentGatewayError(
                provider_failure_code(status, content),
                requests=self.requests,
                accepted_unknown=status >= 500 or status == 408,
                http_status=status,
            )

    async def handle_async_request(self, request):
        if request.method != "POST" or str(request.url) != self.url or self.attempts:
            self.last_error = AgentGatewayError("agent_request_limit", requests=self.requests)
            raise self.last_error
        try:
            data = await request.aread()
            if len(data) > MAX_REQUEST_BYTES:
                raise AgentGatewayError("agent_request_too_large")
            body = json.loads(data)
            if not isinstance(body, dict) or type(body.get("stream", False)) is not bool:
                raise AgentGatewayError("unsupported_agent_request")
        except AgentGatewayError as error:
            self.last_error = error
            raise
        except (ValueError, TypeError):
            self.last_error = AgentGatewayError("invalid_agent_request")
            raise self.last_error from None
        # SafeTransport re-encodes JSON, so SDK framing headers cannot be forwarded.
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower()
            not in {"content-length", "transfer-encoding", "content-encoding", "connection", "host"}
        }
        if not self.secret:
            headers.pop("authorization", None)
        headers.update(self.credential_headers)
        self.attempts += 1
        envelope = {
            "codec": "agent.request",
            "version": 1,
            "framework_version": FRAMEWORK_VERSION,
            "protocol": self.protocol,
            "url": self.url,
            "body": body,
        }
        loop = asyncio.get_running_loop()

        async def admit():
            await self.checkpoint(
                self.on_request, _json_value(envelope), "agent_request_checkpoint_failed"
            )
            if time.monotonic() >= self.deadline:
                self.last_error = AgentGatewayError("timeout")
                raise self.last_error
            self.requests = 0 if isinstance(self.safe, _ReplaySafeTransport) else 1

        def before_send():
            future = asyncio.run_coroutine_threadsafe(admit(), loop)
            try:
                future.result(timeout=max(0.01, self.deadline - time.monotonic()))
            except AgentGatewayError:
                raise
            except Exception:
                future.cancel()
                self.last_error = AgentGatewayError("timeout", accepted_unknown=bool(self.requests))
                raise self.last_error from None

        if body.get("stream"):
            self.queue = asyncio.Queue(maxsize=8)

            def emit(kind, value):
                if self.stop.is_set():
                    raise GenerationError("agent_stream_cancelled", accepted_unknown=True)
                future = asyncio.run_coroutine_threadsafe(self.queue.put((kind, value)), loop)
                try:
                    future.result(timeout=max(0.01, self.deadline - time.monotonic()))
                except Exception:
                    future.cancel()
                    raise GenerationError("timeout", accepted_unknown=True) from None

            async def feed():
                try:
                    status, response_headers, content = await asyncio.to_thread(
                        self.safe.request,
                        "POST",
                        self.url,
                        headers=headers,
                        body=body,
                        max_bytes=self.max_response_bytes,
                        deadline=self.deadline,
                        on_headers=lambda status, values: emit("headers", (status, values)),
                        on_chunk=lambda chunk: emit("chunk", chunk),
                        on_send=before_send,
                        read_error_body=True,
                    )
                    await self.save_response(status, response_headers, content)
                    await self.queue.put(("done", None))
                except asyncio.CancelledError:
                    self.stop.set()
                    raise
                except Exception as error:
                    self.last_error = error
                    await self.queue.put(("error", error))

            self.feed_task = asyncio.create_task(feed())
            kind, value = await self.queue.get()
            if kind == "error":
                raise value
            status, response_headers = value
            content_type = _raw_response(status, response_headers, b"")["content_type"]
            return httpx2.Response(
                status,
                headers={"Content-Type": content_type},
                stream=_CapturedStream(self),
                request=request,
            )
        try:
            status, response_headers, content = await asyncio.to_thread(
                self.safe.request,
                "POST",
                self.url,
                headers=headers,
                body=body,
                max_bytes=self.max_response_bytes,
                deadline=self.deadline,
                on_send=before_send,
                read_error_body=True,
            )
        except GenerationError as error:
            self.last_error = error
            raise
        await self.save_response(status, response_headers, content)
        return httpx2.Response(
            status,
            headers={"Content-Type": self.raw_response["content_type"]},
            content=content,
            request=request,
        )

    async def aclose(self):
        if self.feed_task is not None and not self.feed_task.done():
            self.stop.set()
            self.feed_task.cancel()
            await asyncio.gather(self.feed_task, return_exceptions=True)


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
            return AgentSegmentResult(
                output=output,
                history=normalized_history,
                usage=_json_value(usage),
                protocol=protocol,
                requests=bridge.requests,
                streaming=stream
                and bridge.raw_response["content_type"].split(";", 1)[0].strip().lower()
                == "text/event-stream",
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
