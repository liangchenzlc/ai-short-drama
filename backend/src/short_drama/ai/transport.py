"""Bounded HTTP with validated DNS pinning and no automatic generation retries."""

import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import urllib3
from urllib3.exceptions import HTTPError, NewConnectionError, TimeoutError

from .types import GenerationError


@dataclass
class MultipartBody:
    fields: list[tuple[str, str | tuple[str, bytes, str]]]


def validated_url(value, *, query=False):
    try:
        if not isinstance(value, str) or len(value) > 16384:
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or "#" in value
            or (not query and "?" in value)
            or "\\" in value
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not host:
            raise ValueError
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        netloc = f"[{host}]" if ":" in host else host
        if port and port != (443 if parts.scheme == "https" else 80):
            netloc += f":{port}"
        return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError, TypeError):
        raise GenerationError("unsafe_address") from None


class SafeTransport:
    def __init__(self, settings):
        self.settings = settings

    def request(self, method, url, *, headers=None, body=None, max_bytes, deadline, query=False):
        url = validated_url(url, query=query)
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            addresses = list(
                dict.fromkeys(
                    row[4][0]
                    for row in socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
                )
            )
        except OSError:
            raise GenerationError("dns_error", retryable=True) from None
        allowed = getattr(self.settings, "generation_allowed_hosts", None)
        if allowed is None:
            allowed = getattr(self.settings, "model_discovery_allowed_hosts", [])
        try:
            if not addresses or (
                parts.hostname not in allowed
                and any(not ipaddress.ip_address(address).is_global for address in addresses)
            ):
                raise GenerationError("unsafe_address")
        except ValueError:
            raise GenerationError("unsafe_address") from None
        content_type = "application/json"
        if isinstance(body, MultipartBody):
            encoded, content_type = urllib3.encode_multipart_formdata(body.fields)
        else:
            encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers = {
            "Accept": "application/json" if body is not None else "*/*",
            "Accept-Encoding": "identity",
            "User-Agent": "ShortDrama-Generation/1.0",
            **(headers or {}),
            "Host": parts.netloc,
        }
        if encoded is not None:
            request_headers["Content-Type"] = content_type
        path = urlunsplit(("", "", parts.path, parts.query, ""))
        # Even connection errors do not cause an implicit second POST. GET may try other pinned IPs.
        candidates = addresses[:1] if method == "POST" else addresses[:4]
        last_error = None
        for address in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GenerationError("timeout", retryable=method != "POST")
            options = {"host": address, "port": port, "maxsize": 1}
            pool = (
                urllib3.HTTPSConnectionPool(
                    **options,
                    server_hostname=parts.hostname,
                    assert_hostname=parts.hostname,
                    cert_reqs="CERT_REQUIRED",
                )
                if parts.scheme == "https"
                else urllib3.HTTPConnectionPool(**options)
            )
            response = None
            try:
                response = pool.request(
                    method,
                    path,
                    headers=request_headers,
                    body=encoded,
                    redirect=False,
                    retries=False,
                    preload_content=False,
                    timeout=urllib3.Timeout(
                        total=remaining, connect=min(10, remaining), read=remaining
                    ),
                )
                response_headers = dict(response.headers)
                # Error bodies carry no useful trusted data; do not read or reflect them.
                if response.status < 200 or response.status >= 300:
                    return response.status, response_headers, b""
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise GenerationError("response_too_large", accepted_unknown=method == "POST")
                data = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise GenerationError(
                            "timeout", accepted_unknown=method == "POST", retryable=method != "POST"
                        )
                    chunk = response.read1(
                        min(65536, max_bytes + 1 - len(data)), decode_content=True
                    )
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise GenerationError(
                            "response_too_large", accepted_unknown=method == "POST"
                        )
                return response.status, response_headers, bytes(data)
            except NewConnectionError:
                last_error = GenerationError("connection_error", retryable=True)
            except TimeoutError:
                last_error = GenerationError(
                    "timeout", accepted_unknown=method == "POST", retryable=method != "POST"
                )
            except (HTTPError, OSError, ValueError):
                last_error = GenerationError(
                    "transport_error", accepted_unknown=method == "POST", retryable=method != "POST"
                )
            finally:
                if response is not None:
                    response.close()
                    response.release_conn()
                pool.close()
        raise last_error from None

    def download_media(self, url, max_bytes, *, deadline=None):
        if not isinstance(max_bytes, int) or not 0 < max_bytes <= 1024**3:
            raise GenerationError("invalid_media_limit")
        download_deadline = time.monotonic() + getattr(
            self.settings, "generation_download_timeout", 60
        )
        deadline = min(deadline, download_deadline) if deadline is not None else download_deadline
        current = validated_url(url, query=True)
        for _ in range(4):
            # No caller-controlled headers and no model credential can reach this path.
            status, headers, data = self.request(
                "GET",
                current,
                max_bytes=max_bytes,
                deadline=deadline,
                query=True,
            )
            if status in (301, 302, 303, 307, 308):
                location = next((v for k, v in headers.items() if k.lower() == "location"), None)
                if not location:
                    raise GenerationError("invalid_redirect")
                target = validated_url(urljoin(current, location), query=True)
                if current.startswith("https:") and not target.startswith("https:"):
                    raise GenerationError("unsafe_address")
                current = target
                continue
            if status != 200:
                raise GenerationError("download_failed", retryable=status == 429 or status >= 500)
            mime = next(
                (v for k, v in headers.items() if k.lower() == "content-type"),
                "application/octet-stream",
            )
            return data, mime.split(";", 1)[0].strip().lower()
        raise GenerationError("too_many_redirects")
