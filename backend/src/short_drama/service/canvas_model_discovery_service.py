"""按源合同读取上游模型目录；不保存草稿、不发起推理、不跟随重定向。"""

import json
import time
from types import SimpleNamespace

from pydantic import ValidationError

from short_drama.ai.transport import SafeTransport
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_catalog import CanvasChannelModelsRequest
from short_drama.schemas.canvas_model_catalog import CanvasChannelHeader, validate_channel_headers

from .canvas_catalog_payload import parse_catalog
from .model_discovery_service import ModelDiscoveryError, normalize_base_url

MAX_BYTES = 2 * 1024 * 1024
API_PREFIXES = ("/api/plan/v3", "/api/v3", "/api/v1", "/v1beta", "/v1", "/v2", "/v3")
ERROR_MESSAGES = {
    "address": "模型服务地址无效或不允许访问，请检查 Base URL",
    "credential": "请填写有效的 API Key 或渠道鉴权参数",
    "format": "该接口协议不支持读取模型目录",
    "auth": "模型服务鉴权失败，请检查 API Key",
    "unsupported": "模型服务未提供 /models 接口",
    "rate_limit": "模型服务请求过于频繁或额度不足",
    "redirect": "模型服务返回重定向，请直接填写最终目录服务地址",
    "timeout": "读取模型目录超时，请检查服务和网络",
    "too_large": "模型服务目录超过大小限制",
    "invalid_response": "模型服务返回的目录格式无效",
    "unavailable": "连接模型服务失败，请检查渠道地址和网络",
}


def catalog_error(reason: str, status: int = 502) -> WorkflowError:
    return WorkflowError("canvas_model_catalog_" + reason, ERROR_MESSAGES[reason], status)


def catalog_url(base: str, api_format: str) -> str:
    if api_format == "gemini":
        return base + ("" if base.lower().endswith("/v1beta") else "/v1beta") + "/models"
    return base + ("" if base.lower().endswith(API_PREFIXES) else "/v1") + "/models"


class CanvasModelDiscoveryService:
    def __init__(self, settings, catalog):
        self.catalog = catalog
        # Discovery must use its own allowlist; generation access is not directory access.
        self.transport = SafeTransport(
            SimpleNamespace(
                generation_allowed_hosts=settings.model_discovery_allowed_hosts,
            )
        )

    def discover(self, payload: CanvasChannelModelsRequest) -> dict:
        payload = CanvasChannelModelsRequest.model_validate(
            payload.model_dump(by_alias=True, exclude_unset=True)
        )
        if payload.api_format == "claude":
            raise catalog_error("format", 422)
        try:
            base = normalize_base_url(payload.base_url)
        except ModelDiscoveryError:
            raise catalog_error("address", 400) from None
        saved = (
            self.catalog.discovery_credentials(payload.channel_id, payload.credential_ref, base)
            or {}
        )
        key = (payload.api_key.get_secret_value() if payload.api_key else "").strip() or saved.get(
            "apiKey", ""
        ).strip()
        if not key or len(key) > 16384 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise catalog_error("credential", 422)
        old_headers = {name.lower(): value for name, value in saved.get("headers", {}).items()}
        chosen_headers = payload.headers
        headers = {"Accept": "application/json"}
        if payload.api_format == "gemini":
            headers["x-goog-api-key"] = key
        else:
            headers["Authorization"] = "Bearer " + key
        secrets = [key]
        try:
            if "headers" not in payload.model_fields_set:
                chosen_headers = [
                    CanvasChannelHeader(name=name, value=value)
                    for name, value in saved.get("headers", {}).items()
                ]
            restored = []
            for header in chosen_headers:
                value = header.value.get_secret_value() or old_headers.get(header.name.lower(), "")
                validated = CanvasChannelHeader(name=header.name, value=value)
                value = validated.value.get_secret_value()
                restored.append(validated)
                headers[validated.name] = value.encode("utf-8") if not value.isascii() else value
                if value:
                    secrets.append(value)
            validate_channel_headers(restored)
        except (ValidationError, ValueError):
            raise catalog_error("credential", 422) from None
        try:
            status, _, data = self.transport.request(
                "GET",
                catalog_url(base, payload.api_format),
                headers=headers,
                max_bytes=MAX_BYTES,
                deadline=time.monotonic() + 12,
            )
        except GenerationError as error:
            reason = {
                "unsafe_address": "address",
                "timeout": "timeout",
                "response_too_large": "too_large",
            }.get(error.code, "unavailable")
            raise catalog_error(
                reason, 400 if reason == "address" else 504 if reason == "timeout" else 502
            ) from None
        if status in (401, 403):
            raise catalog_error("auth")
        if status in (404, 405):
            raise catalog_error("unsupported")
        if status == 429:
            raise catalog_error("rate_limit")
        if 300 <= status < 400:
            raise catalog_error("redirect")
        if not 200 <= status < 300:
            raise catalog_error("unavailable")
        try:
            models = parse_catalog(json.loads(data), payload.api_format, secrets)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise catalog_error("invalid_response") from None
        return {"models": models}
