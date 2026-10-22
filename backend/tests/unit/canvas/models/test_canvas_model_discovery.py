import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.schemas.canvas_catalog import CanvasChannelModelsRequest
from short_drama.service.canvas_model_discovery_service import CanvasModelDiscoveryService


class Catalog:
    def __init__(self, credentials=None):
        self.credentials = credentials
        self.calls = []

    def discovery_credentials(self, channel_id, credential_ref, base_url):
        self.calls.append((channel_id, credential_ref, base_url))
        if credential_ref == "host:other-user":
            raise NotFound("本人渠道不存在")
        return self.credentials


@pytest.fixture
def server():
    calls = []
    state = {"status": 200, "body": {"data": [{"id": "z"}, {"id": "a"}]}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, dict(self.headers)))
            body = json.dumps(state["body"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if state["status"] == 302:
                self.send_header("Location", "/stolen-key")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", calls, state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def service(catalog=None, *, allow_local=True):
    settings = SimpleNamespace(
        model_discovery_allowed_hosts=["127.0.0.1"] if allow_local else [],
        generation_allowed_hosts=["127.0.0.1"],
    )
    return CanvasModelDiscoveryService(settings, catalog or Catalog())


def request(base, **changes):
    return CanvasChannelModelsRequest(
        baseUrl=base, apiKey="fixture-private-key", apiFormat="openai", **changes
    )


@pytest.mark.parametrize("path", ["", "/custom", "/v1", "/api/v3", "/custom/v2"])
def test_discovery_uses_source_endpoint_and_only_real_bounded_get(server, path):
    base, calls, _ = server
    result = service().discover(request(base + path))
    assert [item["id"] for item in result["models"]] == ["a", "z"]
    suffix = "/models" if path.endswith(("/v1", "/v2", "/v3")) else "/v1/models"
    assert calls[0][0:2] == ("GET", path + suffix)
    assert calls[0][2]["Authorization"] == "Bearer fixture-private-key"
    assert "fixture-private-key" not in json.dumps(result)


def test_saved_secrets_and_masked_headers_are_read_only_memory_inputs(server):
    base, calls, _ = server
    catalog = Catalog({"apiKey": "saved-private-key", "headers": {"X-Private": "saved-value"}})
    payload = CanvasChannelModelsRequest(
        baseUrl=base,
        apiKey="",
        apiFormat="openai",
        channelId="mine",
        credentialRef="host:mine",
        headers=[{"name": "X-Private", "value": ""}],
    )
    result = service(catalog).discover(payload)
    assert catalog.calls == [("mine", "host:mine", base)]
    assert calls[0][2]["Authorization"] == "Bearer saved-private-key"
    assert calls[0][2]["X-Private"] == "saved-value"
    assert "saved-private-key" not in json.dumps(result)
    assert "saved-value" not in json.dumps(result)
    assert payload.api_key.get_secret_value() == ""
    assert catalog.credentials["headers"] == {"X-Private": "saved-value"}


def test_other_actor_credential_ref_is_rejected_before_http(server):
    base, calls, _ = server
    with pytest.raises(NotFound):
        service().discover(request(base, credentialRef="host:other-user"))
    assert calls == []


def test_explicit_null_key_never_restores_saved_key_or_calls_upstream(server):
    base, calls, _ = server
    catalog = Catalog({"apiKey": "saved-private-key", "headers": {"X-Private": "saved-value"}})
    payload = CanvasChannelModelsRequest(
        baseUrl=base, apiKey=None, channelId="host-17", credentialRef="host:17"
    )
    with pytest.raises(WorkflowError) as caught:
        service(catalog).discover(payload)
    assert caught.value.code == "canvas_model_catalog_credential"
    assert calls == []
    assert catalog.credentials == {
        "apiKey": "saved-private-key",
        "headers": {"X-Private": "saved-value"},
    }


def test_private_destination_is_blocked_even_when_generation_allowlist_would_allow_it(server):
    base, calls, _ = server
    with pytest.raises(WorkflowError) as caught:
        service(allow_local=False).discover(request(base))
    assert caught.value.code == "canvas_model_catalog_address"
    assert calls == []


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "auth"),
        (403, "auth"),
        (404, "unsupported"),
        (429, "rate_limit"),
        (500, "unavailable"),
        (302, "redirect"),
    ],
)
def test_errors_are_safe_and_redirects_never_followed(server, status, reason):
    base, calls, state = server
    state.update(status=status, body={"error": {"message": "fixture-private-key"}})
    with pytest.raises(WorkflowError) as caught:
        service().discover(request(base))
    assert caught.value.code == "canvas_model_catalog_" + reason
    assert "fixture-private-key" not in str(caught.value)
    assert len(calls) == 1


def test_gemini_format_uses_source_v1beta_and_google_auth(server):
    base, calls, state = server
    state["body"] = {"models": [{"name": "models/gemini-image"}]}
    result = service().discover(
        CanvasChannelModelsRequest(
            baseUrl=base,
            apiKey="fixture-private-key",
            apiFormat="gemini",
        )
    )
    assert result == {"models": [{"id": "gemini-image"}]}
    assert calls[0][1] == "/v1beta/models"
    assert calls[0][2]["x-goog-api-key"] == "fixture-private-key"
    assert "Authorization" not in calls[0][2]


def test_metadata_options_defaults_and_explicit_zero_survive_projection(server):
    base, _, state = server
    state["body"] = {
        "data": [
            {
                "id": "video",
                "display_name": "Video",
                "model_type": "VIDEO",
                "supported_endpoint_types": [" video-generation ", "video-generation"],
                "default_parameters": {"aspect_ratio": "16:9", "duration_seconds": "5"},
                "options": {"aspect_ratio": [{"value": "16:9", "label": "Wide"}]},
                "supports_images": False,
                "min_images": 0,
                "max_images": 2,
            }
        ]
    }
    result = service().discover(request(base))["models"][0]
    assert result == {
        "id": "video",
        "displayName": "Video",
        "modelType": "video",
        "supportedEndpointTypes": ["video-generation"],
        "defaultParameters": {"aspectRatio": "16:9", "durationSeconds": "5"},
        "options": {"aspectRatio": [{"value": "16:9", "label": "Wide"}]},
        "supportsImages": False,
        "minImages": 0,
        "maxImages": 2,
    }


def test_reflected_key_or_header_secret_never_reaches_client(server):
    base, _, state = server
    state["body"] = {
        "data": [
            {"id": "leak-fixture-private-key"},
            {"id": "safe", "display_name": "private-header"},
            {"id": "valid"},
        ]
    }
    result = service().discover(
        request(base, headers=[{"name": "X-Private", "value": "private-header"}])
    )
    assert result == {"models": [{"id": "valid"}]}


@pytest.mark.parametrize("body", [{"message": "no catalog"}, {"data": "bad"}, {"data": [1]}])
def test_invalid_catalog_is_not_empty_success(server, body):
    base, _, state = server
    state["body"] = body
    with pytest.raises(WorkflowError) as caught:
        service().discover(request(base))
    assert caught.value.code == "canvas_model_catalog_invalid_response"


def test_oversized_catalog_is_rejected(server):
    base, _, state = server
    state["body"] = {"data": [{"id": "x" * (2 * 1024 * 1024)}]}
    with pytest.raises(WorkflowError) as caught:
        service().discover(request(base))
    assert caught.value.code == "canvas_model_catalog_too_large"


@pytest.mark.parametrize(
    "changes",
    [
        {"headers": [{"name": "Host", "value": "evil"}]},
        {"headers": [{"name": "X-Key", "value": "bad\r\nInjected: yes"}]},
        {"headers": [{"name": "X-Key", "value": "a"}, {"name": "x-key", "value": "b"}]},
    ],
)
def test_request_rejects_unsafe_headers(changes):
    with pytest.raises(ValidationError):
        request("https://example.com", **changes)


def test_mixed_public_private_dns_blocks_entire_destination_before_http(server, monkeypatch):
    base, calls, _ = server
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _host, port, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in ("8.8.8.8", "127.0.0.1")
        ],
    )
    with pytest.raises(WorkflowError) as caught:
        service(allow_local=False).discover(request(base.replace("127.0.0.1", "mixed.example")))
    assert caught.value.code == "canvas_model_catalog_address"
    assert calls == []


@pytest.mark.parametrize("payload", [{"apiKey": ""}, {"apiFormat": "claude"}])
def test_missing_key_or_source_unsupported_format_never_requests_provider(server, payload):
    base, calls, _ = server
    value = {"baseUrl": base, "apiKey": "fixture-private-key", "apiFormat": "openai", **payload}
    with pytest.raises(WorkflowError) as caught:
        service().discover(CanvasChannelModelsRequest.model_validate(value))
    assert caught.value.status_code == 422
    assert calls == []


def test_draft_secret_overrides_saved_value_without_mutating_saved_catalog(server):
    base, calls, _ = server
    catalog = Catalog({"apiKey": "old-private-key", "headers": {"X-Private": "old-value"}})
    service(catalog).discover(
        request(
            base,
            channelId="mine",
            credentialRef="host:mine",
            headers=[
                {"name": "X-Private", "value": "draft-private-value"},
            ],
        )
    )
    assert calls[0][2]["Authorization"] == "Bearer fixture-private-key"
    assert calls[0][2]["X-Private"] == "draft-private-value"
    assert catalog.credentials == {
        "apiKey": "old-private-key",
        "headers": {"X-Private": "old-value"},
    }


def test_omitted_headers_reuse_saved_secrets_but_explicit_empty_list_clears_request_headers(server):
    base, calls, _ = server
    catalog = Catalog({"apiKey": "old-private-key", "headers": {"X-Private": "old-value"}})
    service(catalog).discover(request(base, channelId="mine"))
    assert calls[0][2]["X-Private"] == "old-value"
    service(catalog).discover(request(base, channelId="mine", headers=[]))
    assert "X-Private" not in calls[1][2]


def video_capability():
    return {
        "references": {"minImages": 0, "maxImages": 2, "maxVideoBytes": 0},
        "duration": {"selection": "enum", "values": [5, 10], "default": 5},
        "ratios": ["16:9"],
        "defaultRatio": "16:9",
        "resolutions": ["720p"],
        "defaultResolution": "720p",
        "operations": ["text-to-video"],
        "defaultOperation": "text-to-video",
        "generateAudio": {"supported": True, "default": False},
        "watermark": {"supported": False, "default": False},
        "durationSupported": True,
    }


def test_video_catalog_preserves_explicit_zero_and_drops_omitted_limits(server):
    base, _, state = server
    video = video_capability()
    state["body"] = {
        "data": [{"id": "video", "video_capabilities": video, "video_capabilities_version": "v2"}]
    }
    result = service().discover(request(base))["models"][0]
    assert result["videoCapabilities"] == video
    assert "maxAudios" not in result["videoCapabilities"]["references"]
    assert result["videoCapabilitiesVersion"] == "v2"


@pytest.mark.parametrize(
    "key,value",
    [
        ("references", {"minImages": 2, "maxImages": 1}),
        ("duration", {"selection": "range", "min": 2, "max": 6, "step": 2, "default": 3}),
        ("defaultOperation", "missing"),
        ("generateAudio", {"supported": "true", "default": False}),
        ("durationSupported", 1),
    ],
)
def test_invalid_video_metadata_is_dropped_without_inventing_defaults(server, key, value):
    base, _, state = server
    video = {**video_capability(), key: value}
    state["body"] = {
        "data": [{"id": "video", "video_capabilities": video, "video_capabilities_version": "v2"}]
    }
    assert service().discover(request(base)) == {"models": [{"id": "video"}]}
