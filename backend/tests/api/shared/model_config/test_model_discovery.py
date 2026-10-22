import asyncio
import socket

import httpx
import pytest

from short_drama.core.config import Settings
from short_drama.main import create_app


def discover(payload, allow_local=False):
    async def run():
        settings = Settings(
            _env_file=None,
            model_discovery_allowed_hosts=["127.0.0.1", "catalog.invalid"] if allow_local else [],
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://testserver"
            ) as client:
                return await client.post("/api/v1/ai-model-configs/discover-models", json=payload)

    return asyncio.run(run())


@pytest.mark.parametrize("path", ["", "/v1/", "/api/v3", "/fallback"])
def test_discovery_uses_real_http_and_normalizes_models(upstream, path):
    base, calls, _ = upstream
    result = discover({"base_url": base + path, "apikey": "test-only-secret"}, allow_local=True)
    assert result.status_code == 200
    assert result.json()["items"] == [{"id": "a-model"}, {"id": "z-model"}]
    expected = {
        "": "/v1/models",
        "/v1/": "/v1/models",
        "/api/v3": "/api/v3/models",
        "/fallback": "/fallback/models",
    }[path]
    assert calls[-1] == (expected, "Bearer test-only-secret")
    assert "test-only-secret" not in result.text


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "model_discovery_auth"),
        (403, "model_discovery_auth"),
        (404, "model_discovery_unsupported"),
        (429, "model_discovery_rate_limit"),
        (500, "model_discovery_unavailable"),
        (302, "model_discovery_redirect"),
    ],
)
def test_discovery_errors_are_actionable_and_never_echo_upstream(upstream, status, code):
    base, calls, state = upstream
    state.update(status=status, body={"error": "test-only-secret"})
    result = discover({"base_url": base, "apikey": "test-only-secret"}, allow_local=True)
    assert result.status_code == 502
    assert result.json()["error"]["code"] == code
    assert "test-only-secret" not in result.text
    if status != 404:
        assert len(calls) == 1


def test_discovery_rejects_private_destination_before_sending_key(upstream):
    base, calls, _ = upstream
    response = discover({"base_url": base, "apikey": "test-only-secret"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "model_discovery_address"
    assert calls == []


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:secret@example.com",
        "https://example.com?key=secret",
        "https://example.com/#secret",
        "",
    ],
)
def test_invalid_addresses_are_rejected_without_echo(url):
    response = discover({"base_url": url})
    assert response.status_code in (400, 422)
    assert "secret" not in response.text


def test_malformed_model_response_is_not_reported_as_empty_success(upstream):
    base, _, state = upstream
    state["body"] = {"message": "not a model catalog"}
    response = discover({"base_url": base}, allow_local=True)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_discovery_invalid_response"


def test_empty_catalog_is_valid(upstream):
    base, _, state = upstream
    state["body"] = {"data": []}
    response = discover({"base_url": base}, allow_local=True)
    assert response.status_code == 200 and response.json()["items"] == []


@pytest.mark.parametrize(
    "body, expected, truncated",
    [
        ({"models": [{"slug": "glm-example"}]}, "glm-example", False),
        (
            {"models": [{"name": "models/gemini-example"}], "nextPageToken": "another-page"},
            "gemini-example",
            True,
        ),
        ({"data": [{"id": "claude-example"}], "has_more": True}, "claude-example", True),
    ],
)
def test_alternate_catalog_formats(upstream, body, expected, truncated):
    base, _, state = upstream
    state["body"] = body
    response = discover({"base_url": base}, allow_local=True)
    assert response.status_code == 200
    assert response.json() == {"items": [{"id": expected}], "truncated": truncated}


def test_oversized_response_is_bounded(upstream):
    base, _, state = upstream
    state["body"] = {"data": [{"id": "x" * (2 * 1024 * 1024)}]}
    response = discover({"base_url": base}, allow_local=True)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_discovery_too_large"


def test_catalog_does_not_reflect_submitted_secret(upstream):
    base, _, state = upstream
    state["body"] = {"data": [{"id": "reflected-test-secret"}, {"id": "safe-model"}]}
    response = discover({"base_url": base, "apikey": "test-secret"}, allow_local=True)
    assert response.status_code == 200
    assert response.json()["items"] == [{"id": "safe-model"}]
    assert "test-secret" not in response.text


def test_discovery_tries_second_validated_ip_if_first_cannot_connect(upstream, monkeypatch):
    base, _, _ = upstream
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host == "catalog.invalid":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in ("127.0.0.2", "127.0.0.1")
            ]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    response = discover(
        {"base_url": base.replace("127.0.0.1", "catalog.invalid")}, allow_local=True
    )
    assert response.status_code == 200 and len(response.json()["items"]) == 2
