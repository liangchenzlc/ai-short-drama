"""单次受保护发送、流式回执捕获与敏感内容屏蔽。"""

import asyncio
import base64
import json
import threading
import time

import httpx2
from openai._streaming import SSEDecoder
from pydantic_ai.messages import (
    TextPart,
)

from short_drama.agent.gateway.contracts import (
    FRAMEWORK_VERSION,
    MAX_REQUEST_BYTES,
    AgentGatewayError,
    provider_failure_code,
)
from short_drama.agent.gateway.history import _json_value
from short_drama.ai.types import GenerationError


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
    evidence = {
        "usage_reported": isinstance(usage, dict),
        "output_tokens_reported": isinstance(usage, dict)
        and type(usage.get(field_name)) is int
        and usage[field_name] >= 0,
    }
    cached = None
    if isinstance(usage, dict):
        details = usage.get(
            "prompt_tokens_details" if protocol == "openai_chat.v1" else "input_tokens_details"
        )
        if isinstance(details, dict) and type(details.get("cached_tokens")) is int:
            cached = details["cached_tokens"]
    if cached is not None and cached >= 0:
        evidence["cached_tokens"] = cached
    return evidence, terminal


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


def _narration_text(response, secrets):
    """Text the model emitted alongside tool calls, redacted and trimmed."""
    parts = [part.content for part in response.parts if isinstance(part, TextPart) and part.content]
    if not parts:
        return ""
    return _TextRedactor(secrets).add("".join(parts), final=True).strip()


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
        except AgentGatewayError as error:
            self.last_error = AgentGatewayError(
                error.code,
                requests=self.requests,
                accepted_unknown=bool(self.requests) or error.accepted_unknown,
            )
            raise self.last_error from None
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
