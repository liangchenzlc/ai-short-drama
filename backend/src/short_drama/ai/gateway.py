"""Synchronous calls only; durable scheduling and recording belong to the runtime."""

import json
import time

from .adapters import (
    ADAPTER_TYPES,
    build_submission,
    parse_result,
    poll_endpoint,
    resolved_parameters,
    select_adapter,
    validate_request,
)
from .streaming import decode_text_stream
from .transport import SafeTransport
from .types import GenerationError


def _credential(value):
    secret = value.get_secret_value() if hasattr(value, "get_secret_value") else value or ""
    if (
        not isinstance(secret, str)
        or len(secret) > 16384
        or any(ord(c) < 33 or ord(c) > 126 for c in secret)
    ):
        raise GenerationError("invalid_credential")
    return secret


def _safe_value(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, dict):
        return {_safe_value(k, secret): _safe_value(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_value(item, secret) for item in value]
    return value


class GenerationGateway:
    def __init__(self, settings):
        self.settings = settings
        self.transport = SafeTransport(settings)

    def submit(self, snapshot, request_data, credential, adapter=None):
        adapter = adapter or select_adapter(snapshot)
        url, headers, body = build_submission(snapshot, request_data, adapter)
        if snapshot.get("service_type") == "text" and body.get("stream"):
            headers["Accept"] = "text/event-stream, application/json"
        secret = _credential(credential)
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        body_response = self._json_request("POST", url, snapshot, headers, body)
        result = parse_result(body_response, adapter, submitted=True)
        result.resolved_parameters = resolved_parameters(body)
        return self._sanitize(result, secret, submitted=True)

    def validate(self, snapshot, request_data, adapter=None):
        return validate_request(snapshot, request_data, adapter)["resolved_parameters"]

    def poll(self, snapshot, provider_task_id, credential, adapter):
        if ADAPTER_TYPES.get(adapter) != snapshot.get("service_type"):
            raise GenerationError("unsupported_protocol")
        secret = _credential(credential)
        url = poll_endpoint(snapshot, provider_task_id, adapter)
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        body = self._json_request("GET", url, snapshot, headers, None)
        result = parse_result(body, adapter, submitted=False, task_id=provider_task_id)
        return self._sanitize(result, secret, submitted=False)

    def _json_request(self, method, url, snapshot, headers, body):
        default_budget = {"text": 3600, "image": 300, "video": 1800}.get(
            snapshot.get("service_type"), 3600
        )
        try:
            budget = float(snapshot.get("budget_seconds", default_budget))
            if not 0 < budget <= 86400:
                raise ValueError
        except (TypeError, ValueError):
            raise GenerationError("invalid_budget") from None
        # Each poll is bounded separately; the runtime owns the overall task deadline.
        if method == "GET":
            budget = min(budget, 30)
        max_bytes = getattr(self.settings, "generation_max_response_bytes", 8 * 1024**2)
        if snapshot.get("service_type") == "image":
            image_limit = getattr(self.settings, "generation_max_image_bytes", 50 * 1024**2)
            max_bytes = max(max_bytes, 4 * ((image_limit + 2) // 3 * 4) + 65536)
        status, response_headers, data = self.transport.request(
            method,
            url,
            headers=headers,
            body=body,
            max_bytes=max_bytes,
            deadline=time.monotonic() + budget,
        )
        submitting = method == "POST"
        if status in (404, 405):
            # A generic 404 can be a model/business error, not proof of a wrong protocol.
            # Do not authorize a second paid POST from status alone.
            raise GenerationError("provider_endpoint")
        if status in (401, 403):
            raise GenerationError("provider_auth")
        if status == 429:
            raise GenerationError("provider_rate_limit", retryable=True)
        if 300 <= status < 400:
            raise GenerationError("provider_redirect")
        if status >= 500:
            raise GenerationError(
                "upstream_unavailable",
                accepted_unknown=submitting,
                retryable=not submitting,
                http_status=status,
            )
        if status < 200 or status >= 300:
            raise GenerationError("provider_rejected")
        content_type = next(
            (value for key, value in response_headers.items() if key.lower() == "content-type"), ""
        )
        if (
            submitting
            and snapshot.get("service_type") == "text"
            and "text/event-stream" in content_type.lower()
        ):
            return decode_text_stream(data, responses="input" in body)
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise GenerationError(
                "invalid_response", accepted_unknown=submitting, retryable=not submitting
            ) from None

    def _sanitize(self, result, secret, *, submitted):
        # Identifiers/URLs containing a credential cannot safely be persisted or followed.
        if secret and result.provider_task_id and secret in result.provider_task_id:
            raise GenerationError("invalid_response", accepted_unknown=submitted)
        for output in result.outputs:
            if secret and secret in output.get("url", ""):
                raise GenerationError("invalid_response", accepted_unknown=submitted)
        result.text = _safe_value(result.text, secret)
        result.usage = _safe_value(result.usage, secret)
        result.finish_reason = _safe_value(result.finish_reason, secret)
        result.resolved_parameters = _safe_value(result.resolved_parameters, secret)
        return result

    def download_media(self, url, max_bytes):
        return self.transport.download_media(url, max_bytes)
