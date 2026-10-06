"""固定 BeefAPI 企业源的有界设备授权 HTTP，不接收浏览器提供的服务地址。"""

import ipaddress
import json
import socket
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from pydantic import ValidationError

from short_drama.schemas.canvas_beefapi import BeefAPIToken
from short_drama.service.canvas_catalog_payload import parse_catalog

from .transport import SafeTransport, validated_url
from .types import GenerationError

PRODUCTION_ORIGIN = "https://enterprise.beefapi.com"
CREDENTIAL_REF = "beefapi-enterprise"
CHANNEL_ID = "beefapi"
CLIENT_ID = "beeftv-enterprise-v1"


class CanvasBeefAPIError(Exception):
    def __init__(self, code: str, message: str = "企业连接请求失败"):
        super().__init__(message)
        self.code = code


def canonical_origin(test_origin: str = "") -> str:
    if not test_origin:
        return PRODUCTION_ORIGIN
    try:
        raw = test_origin.strip().rstrip("/")
        normalized = validated_url(raw)
        parts = urlsplit(normalized)
        original = urlsplit(raw)
        if original.path or "?" in raw or "#" in raw or original.netloc.endswith(":"):
            raise ValueError
        if raw.lower() == PRODUCTION_ORIGIN:
            return PRODUCTION_ORIGIN
        host = parts.hostname
        if host not in {"localhost", "enterprise.localhost"}:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        return normalized.rstrip("/")
    except (ValueError, GenerationError):
        raise CanvasBeefAPIError("origin_invalid", "测试企业源只允许显式 loopback 地址") from None


def trusted_browser_url(origin: str, value: str, *, wallet=False) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        result = validated_url(value.strip(), query=not wallet)
        parts, root = urlsplit(result), urlsplit(origin)
        if (parts.scheme, parts.netloc) != (root.scheme, root.netloc):
            raise ValueError
        if wallet:
            if parts.path != "/console/topup":
                raise ValueError
        elif parts.path != "/desktop-auth" and not parts.path.startswith("/desktop-auth/"):
            raise ValueError
        if "%" in parts.path or any(part in {".", ".."} for part in parts.path.split("/")):
            raise ValueError
        return result
    except (ValueError, GenerationError):
        raise CanvasBeefAPIError("browser_url_invalid", "企业确认页地址无效") from None


def oauth_error(body) -> str:
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, dict):
        for field in ("code", "error"):
            if isinstance(error.get(field), str) and error[field]:
                return error[field]
    return body.get("error_code") if isinstance(body.get("error_code"), str) else ""


class CanvasBeefAPIClient:
    def __init__(self, *, test_origin="", client_version="dev", hostname=None):
        self.origin = canonical_origin(test_origin)
        self.client_version = client_version
        self.hostname = hostname or socket.gethostname() or "BeefTV"
        host = urlsplit(self.origin).hostname
        self.transport = SafeTransport(
            SimpleNamespace(
                generation_allowed_hosts=[host]
                if test_origin and self.origin != PRODUCTION_ORIGIN
                else []
            )
        )

    @property
    def wallet_url(self):
        return trusted_browser_url(self.origin, self.origin + "/console/topup", wallet=True)

    def _request(self, method, path, *, body=None, key="", max_bytes=1024**2):
        headers = {"Accept": "application/json"}
        if key:
            if len(key.encode()) > 16384 or any(ord(char) < 33 or ord(char) > 126 for char in key):
                raise CanvasBeefAPIError("credential_invalid")
            headers["Authorization"] = "Bearer " + key
        try:
            status, _, raw = self.transport.request(
                method,
                self.origin + path,
                headers=headers,
                body=body,
                max_bytes=max_bytes,
                deadline=time.monotonic() + 20,
                read_error_body=True,
            )
        except GenerationError:
            raise CanvasBeefAPIError("transport_unavailable") from None
        if 300 <= status < 400:
            raise CanvasBeefAPIError("redirect_rejected")
        try:
            return status, json.loads(raw) if raw else {}
        except (ValueError, RecursionError):
            raise CanvasBeefAPIError("response_invalid") from None

    @staticmethod
    def _device_body(device_code):
        return {"client_id": CLIENT_ID, "device_code": device_code}

    def device_code(self):
        status, body = self._request(
            "POST",
            "/api/oauth/device/code",
            body={
                "client_id": CLIENT_ID,
                "scope": "inference",
                "client_version": self.client_version,
                "hostname": self.hostname,
            },
        )
        if status >= 300 or not isinstance(body, dict):
            raise CanvasBeefAPIError("start_failed", "无法开始企业授权")
        for key in ("device_code", "user_code"):
            value = body.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 16384:
                raise CanvasBeefAPIError("device_invalid", "企业授权响应无效")
        for field, default in (("expires_in", 900), ("interval", 5)):
            value = body.get(field)
            body[field] = value if type(value) is int and 0 < value <= 86400 else default
        body["verification_uri"] = trusted_browser_url(
            self.origin, body.get("verification_uri") or self.origin + "/desktop-auth"
        )
        if complete := body.get("verification_uri_complete"):
            body["verification_uri_complete"] = trusted_browser_url(self.origin, complete)
        return body

    def poll_token(self, device_code):
        status, body = self._request(
            "POST", "/api/oauth/device/token", body=self._device_body(device_code)
        )
        if status != 200:
            return None, oauth_error(body) or "invalid_request"
        try:
            token = BeefAPIToken.model_validate(body)
            key = token.api_key.get_secret_value()
            if (
                token.base_url.strip().rstrip("/") != self.origin + "/v1"
                or not key.strip()
                or len(key.encode()) > 16384
                or any(ord(char) < 33 or ord(char) > 126 for char in key)
            ):
                raise ValueError
            public = {"account": token.account.model_dump(), "keyName": token.key_name}
            if key in json.dumps(public, ensure_ascii=False):
                raise ValueError
        except (ValidationError, ValueError, TypeError):
            raise CanvasBeefAPIError("token_invalid", "企业授权响应无效") from None
        return token, ""

    def complete(self, device_code):
        status, body = self._request(
            "POST", "/api/oauth/device/complete", body=self._device_body(device_code)
        )
        if status == 200 and isinstance(body, dict) and body.get("success") is True:
            return
        code = oauth_error(body)
        if status >= 500 or status == 429:
            code = ""
        elif status >= 400 and not code:
            code = "invalid_request"
        if code in {"expired_token", "expired", "invalid_request"}:
            raise CanvasBeefAPIError("ack_expired", "授权已过期，请重新连接")
        if code in {"access_denied", "denied", "rejected"}:
            raise CanvasBeefAPIError("ack_rejected", "授权被拒绝")
        raise CanvasBeefAPIError("ack_transient", "确认企业授权失败")

    def cancel(self, device_code):
        if device_code:
            self._request("POST", "/api/oauth/device/cancel", body=self._device_body(device_code))

    def connection(self, api_key):
        status, body = self._request("GET", "/v1/beeftv/connection", key=api_key)
        return body, status

    def revoke(self, api_key):
        status, _ = self._request("DELETE", "/v1/beeftv/connection", key=api_key)
        if status not in {200, 204, 401}:
            raise CanvasBeefAPIError("revoke_failed", "断开企业连接失败")

    def models(self, api_key):
        status, body = self._request("GET", "/v1/models", key=api_key, max_bytes=4 * 1024**2)
        if status in {401, 403}:
            raise CanvasBeefAPIError("revoked", "连接已失效，请重新连接")
        if status != 200:
            raise CanvasBeefAPIError("catalog_failed", "模型列表读取失败，请重试")
        try:
            models = parse_catalog(body, "openai", [api_key])
            # The source preserves server catalog order when adding managed models.
            ordered = [
                (item.get("id") or item.get("name") or "").strip().removeprefix("models/")
                for item in body["data"]
            ]
            return sorted(models, key=lambda item: ordered.index(item["id"]))
        except (ValueError, TypeError, KeyError, RecursionError):
            raise CanvasBeefAPIError("catalog_invalid", "模型列表读取失败，请重试") from None
