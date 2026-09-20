"""Read a provider's model catalog without persisting configuration or invoking inference."""

import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlsplit, urlunsplit

import urllib3
from urllib3.exceptions import HTTPError, TimeoutError

from short_drama.core.exceptions import BusinessError
from short_drama.schemas.model_discovery import (
    DiscoveredModel,
    ModelDiscoveryRead,
    ModelDiscoveryRequest,
)

MAX_BYTES = 2 * 1024 * 1024
MAX_MODELS = 2000


class ModelDiscoveryError(BusinessError):
    def __init__(self, reason, status_code=502):
        self.code = f"model_discovery_{reason}"
        self.status_code = status_code
        super().__init__(f"Model discovery failed: {reason}")


def normalize_base_url(value):
    try:
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port and port != (443 if parts.scheme == "https" else 80):
            authority += f":{port}"
        return urlunsplit((parts.scheme, authority, parts.path, "", ""))
    except (ValueError, UnicodeError):
        raise ModelDiscoveryError("address", 400) from None


def model_paths(base_url):
    """Preserve custom prefixes and version segments; fallback only on 404/405."""
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    if path.endswith("/models"):
        return [path]
    for suffix in ("/chat/completions", "/responses", "/messages"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if parts.hostname == "generativelanguage.googleapis.com":
        return [f"{path or '/v1beta'}/models"]
    if re.search(r"/v\d+(?:beta\d*)?$", path):
        paths = [f"{path}/models"]
        if not path.endswith("/v1"):
            paths.append(f"{path}/v1/models")
    else:
        paths = [f"{path}/v1/models", f"{path}/models"]
    for suffix in ("/api/claudecode", "/api/anthropic", "/apps/anthropic", "/anthropic"):
        if path.endswith(suffix):
            root = path[: -len(suffix)]
            paths.extend([f"{root}/v1/models", f"{root}/models"])
            break
    return list(dict.fromkeys(paths))[:4]


class ModelDiscoveryService:
    def __init__(self, settings, configurations):
        self.settings = settings
        self.configurations = configurations

    def discover(self, payload: ModelDiscoveryRequest):
        base = normalize_base_url(payload.base_url)
        # Explicit null means no key; omission permits reuse for an unchanged saved URL.
        secret = payload.apikey
        if payload.config_id is not None and "apikey" not in payload.model_fields_set:
            secret = self.configurations.key_for_discovery(payload.config_id, base)
        key = secret.get_secret_value() if secret else ""
        if len(key) > 16384 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ModelDiscoveryError("credential", 400)
        parts = urlsplit(base)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            addresses = list(
                dict.fromkeys(
                    entry[4][0] for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                )
            )
        except OSError:
            raise ModelDiscoveryError("unavailable") from None
        allowed = host in self.settings.model_discovery_allowed_hosts
        if not addresses or (
            not allowed and any(not ipaddress.ip_address(a).is_global for a in addresses)
        ):
            raise ModelDiscoveryError("address", 400)
        headers = {
            "Accept": "application/json",
            "Host": parts.netloc,
            "User-Agent": "ShortDrama-ModelDiscovery/1.0",
        }
        if host == "generativelanguage.googleapis.com":
            if key:
                headers["x-goog-api-key"] = key
        elif host == "api.anthropic.com":
            headers["anthropic-version"] = "2023-06-01"
            if key:
                headers["x-api-key"] = key
        elif key:
            headers["Authorization"] = f"Bearer {key}"
        # Connect only to validated IPs; retry another address after transport failures.
        deadline = time.monotonic() + 12
        last_timeout = False
        for address in addresses[:4]:
            if time.monotonic() >= deadline:
                raise ModelDiscoveryError("timeout", 504)
            options = {"host": address, "port": port, "maxsize": 1}
            if parts.scheme == "https":
                pool = urllib3.HTTPSConnectionPool(
                    **options, server_hostname=host, assert_hostname=host, cert_reqs="CERT_REQUIRED"
                )
            else:
                pool = urllib3.HTTPConnectionPool(**options)
            try:
                return fetch_catalog(pool, model_paths(base), headers, key, deadline)
            except TimeoutError:
                last_timeout = True
            except (HTTPError, OSError, ValueError):
                last_timeout = False
            finally:
                pool.close()
        raise ModelDiscoveryError(
            "timeout" if last_timeout else "unavailable", 504 if last_timeout else 502
        )


def fetch_catalog(pool, paths, headers, key, deadline):
    for path in paths:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelDiscoveryError("timeout", 504)
        response = pool.request(
            "GET",
            path,
            headers=headers,
            redirect=False,
            retries=False,
            preload_content=False,
            timeout=urllib3.Timeout(
                total=remaining, connect=min(3, remaining), read=min(3, remaining)
            ),
        )
        try:
            if response.status in (404, 405):
                continue
            if response.status in (401, 403):
                raise ModelDiscoveryError("auth")
            if response.status == 429:
                raise ModelDiscoveryError("rate_limit")
            if 300 <= response.status < 400:
                raise ModelDiscoveryError("redirect")
            if response.status != 200:
                raise ModelDiscoveryError("unavailable")
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ModelDiscoveryError("timeout", 504)
                chunk = response.read1(64 * 1024, decode_content=True)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ModelDiscoveryError("too_large")
            return parse_models(data, key)
        finally:
            response.close()
            response.release_conn()
    raise ModelDiscoveryError("unsupported")


def parse_models(data, secret=""):
    try:
        body = json.loads(data)
        entries = body.get("data", body.get("models"))
        if not isinstance(entries, list):
            raise ValueError
        identifiers = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError
            identifier = entry.get("id", entry.get("slug", entry.get("name")))
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError
            if identifier.startswith("models/") and "name" in entry and "id" not in entry:
                identifier = identifier.removeprefix("models/")
            if len(identifier) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in identifier):
                continue
            if secret and secret in identifier:
                continue
            identifiers.add(identifier)
        return ModelDiscoveryRead(
            items=[DiscoveredModel(id=value) for value in sorted(identifiers)[:MAX_MODELS]],
            truncated=len(identifiers) > MAX_MODELS
            or bool(body.get("nextPageToken") or body.get("has_more")),
        )
    except (ValueError, TypeError, AttributeError):
        raise ModelDiscoveryError("invalid_response") from None
