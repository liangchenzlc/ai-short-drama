"""Cancellable, DNS-pinned Agent HTTP; only unsent connections may be retried."""

import asyncio
import ipaddress
import json
import logging
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx2

from short_drama.ai.gateway.transport import validated_url
from short_drama.ai.types import GenerationError

logger = logging.getLogger(__name__)
CONNECT_TIMEOUTS = (30, 60, 90, 120, 150, 180)


class AgentHTTPTransport:
    def __init__(self, settings: object):
        self.settings = settings

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        body: dict,
        max_bytes: int,
        on_send: Callable[[], Awaitable[None]],
        on_headers: Callable[[int, dict], Awaitable[None]] | None = None,
        on_chunk: Callable[[bytes], Awaitable[None]] | None = None,
        on_state: Callable[[dict], Awaitable[None]] | None = None,
        timeout_seconds: float = 120,
        retry_connections: bool = True,
    ) -> tuple[int, dict, bytes]:
        url = validated_url(url)
        parts = urlsplit(url)
        content = json.dumps(body, ensure_ascii=False).encode("utf-8")
        timeouts = CONNECT_TIMEOUTS if retry_connections else (min(30, timeout_seconds),)
        sent = False

        async def trace(name: str, info: dict) -> None:
            nonlocal sent
            if name == "http11.send_request_headers.started":
                # TCP/TLS已成功；账本必须落盘后才允许发送第一个请求字节。
                connecting.reschedule(None)
                await on_send()
                sent = True

        for retry_no, connect_timeout in enumerate(timeouts):
            if on_state:
                await on_state(
                    {
                        "status": "retrying" if retry_no else "connecting",
                        "retry_no": retry_no,
                        "max_retries": len(timeouts) - 1,
                        "timeout_seconds": connect_timeout,
                    }
                )
            try:
                async with asyncio.timeout(connect_timeout) as connecting:
                    try:
                        rows = await asyncio.to_thread(
                            socket.getaddrinfo,
                            parts.hostname,
                            parts.port or (443 if parts.scheme == "https" else 80),
                            type=socket.SOCK_STREAM,
                        )
                    except OSError:
                        raise GenerationError("dns_error") from None
                    addresses = list(dict.fromkeys(row[4][0] for row in rows))
                    allowed = getattr(self.settings, "generation_allowed_hosts", [])
                    if not addresses or (
                        parts.hostname not in allowed
                        and any(not ipaddress.ip_address(value).is_global for value in addresses)
                    ):
                        raise GenerationError("unsafe_address")
                    address = addresses[0]
                    host = f"[{address}]" if ":" in address else address
                    if parts.port:
                        host += f":{parts.port}"
                    pinned = urlunsplit((parts.scheme, host, parts.path, parts.query, ""))
                    async with httpx2.AsyncClient(
                        transport=httpx2.AsyncHTTPTransport(retries=0),
                        timeout=httpx2.Timeout(
                            timeout_seconds,
                            connect=connect_timeout,
                        ),
                        follow_redirects=False,
                        trust_env=False,
                    ) as client:
                        request = client.build_request(
                            method,
                            pinned,
                            content=content,
                            headers={
                                **{key.lower(): value for key, value in headers.items()},
                                "host": parts.netloc,
                                "content-type": "application/json",
                                "accept-encoding": "identity",
                            },
                            extensions={"sni_hostname": parts.hostname, "trace": trace},
                        )
                        response = await client.send(request, stream=True)
                        try:
                            response_headers = dict(response.headers)
                            if on_headers:
                                await on_headers(response.status_code, response_headers)
                            error_body = 400 <= response.status_code < 500
                            if not 200 <= response.status_code < 300 and not error_body:
                                return response.status_code, response_headers, b""
                            limit = min(max_bytes, 65536) if error_body else max_bytes
                            data = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=None):
                                data.extend(chunk)
                                if len(data) > limit:
                                    if error_body:
                                        return response.status_code, response_headers, b""
                                    raise GenerationError(
                                        "response_too_large", accepted_unknown=sent
                                    )
                                if on_chunk:
                                    await on_chunk(chunk)
                            return response.status_code, response_headers, bytes(data)
                        finally:
                            await response.aclose()
            except (httpx2.ConnectTimeout, TimeoutError) as error:
                if isinstance(error, TimeoutError) and not connecting.expired():
                    raise
                if sent:
                    raise GenerationError("agent_response_timeout", accepted_unknown=True) from None
                logger.info(
                    "agent_connection_timeout",
                    extra={
                        "retry_no": retry_no,
                        "timeout_seconds": connect_timeout,
                    },
                )
                if retry_no == len(timeouts) - 1:
                    raise GenerationError("agent_connection_timeout") from None
            except httpx2.TimeoutException:
                raise GenerationError("agent_response_timeout", accepted_unknown=sent) from None
            except httpx2.ConnectError:
                raise GenerationError("connection_error", accepted_unknown=sent) from None
            except httpx2.HTTPError:
                raise GenerationError("transport_error", accepted_unknown=sent) from None
        raise AssertionError("Connection retry loop must return or raise")
