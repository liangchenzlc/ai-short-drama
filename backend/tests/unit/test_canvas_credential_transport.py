import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_canvas_generation_parameters import frozen_request, request
from test_generation_adapters import gateway as gateway
from test_generation_adapters import provider as provider
from test_generation_adapters import snapshot

from short_drama.ai.canvas_credentials import CanvasCredentials
from short_drama.ai.types import GenerationError


def credentials():
    return CanvasCredentials(
        apiKey="private-main-key",
        headers=[
            {"name": "X-Waf-Token", "value": "private-header-token"},
            {"name": "User-Agent", "value": "Custom-Canvas/1.0"},
        ],
    )


def frozen(base, kind="text", model="gpt-4.1", scene="canvas_node"):
    return {**snapshot(base, kind, model), "canvas_auth_version": 1, "canvas_auth_scene": scene}


def test_custom_headers_and_primary_bearer_reach_actual_post_without_entering_json(
    gateway, provider
):
    base, state, calls = provider
    state["body"] = {"choices": [{"message": {"content": "safe text"}, "finish_reason": "stop"}]}
    result = gateway.submit(
        frozen(base), {"input": {"messages": [{"role": "user", "content": "hello"}]}}, credentials()
    )
    assert result.text == "safe text"
    assert calls[0][2]["Authorization"] == "Bearer private-main-key"
    assert calls[0][2]["X-Waf-Token"] == "private-header-token"
    assert calls[0][2]["User-Agent"] == "Custom-Canvas/1.0"
    assert "private-main-key" not in json.dumps(calls[0][3])
    assert "private-header-token" not in json.dumps(calls[0][3])


def test_custom_headers_survive_actual_poll(gateway, provider):
    base, state, calls = provider
    state["body"] = {"id": "job-original", "status": "running"}
    result = gateway.poll(
        frozen(base, "video", "doubao-seedance-1-0-pro"),
        "job-original",
        credentials(),
        "ark_video.v1",
    )
    assert result.status == "submitted"
    assert calls[0][0] == "GET"
    assert calls[0][2]["Authorization"] == "Bearer private-main-key"
    assert calls[0][2]["X-Waf-Token"] == "private-header-token"


@pytest.mark.parametrize("status", [400, 429])
@pytest.mark.parametrize("canvas", [False, True])
def test_balance_error_keeps_static_http_classification(gateway, provider, status, canvas):
    base, state, calls = provider
    state.update(status=status, body={"error": {"message": "余额不足 private-header-token"}})
    saved = frozen(base) if canvas else snapshot(base)
    with pytest.raises(GenerationError) as caught:
        gateway.submit(
            saved,
            {"input": {"messages": [{"role": "user", "content": "hello"}]}},
            credentials() if canvas else "private-main-key",
        )
    expected = "provider_rate_limit" if status == 429 else "provider_rejected"
    assert caught.value.code == expected
    assert "private-header-token" not in str(caught.value)
    assert len(calls) == 1


def test_final_supplier_text_and_usage_redact_each_private_header(gateway, provider):
    base, state, _ = provider
    state["body"] = {
        "choices": [
            {
                "message": {"content": "private-main-key/private-header-token"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"diagnostic": "private-header-token"},
    }
    result = gateway.submit(
        frozen(base), {"input": {"messages": [{"role": "user", "content": "hello"}]}}, credentials()
    )
    assert result.text == "[redacted]/[redacted]"
    assert result.usage == {"diagnostic": "[redacted]"}


def test_stream_redacts_header_split_across_supplier_events_before_callback(gateway, provider):
    base, state, _ = provider
    state["headers"] = {"Content-Type": "text/event-stream"}
    chunks = ["safe/private-head", "er-token/private-", "main-key/end"]
    events = [
        {"choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]}
        for chunk in chunks
    ]
    events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    state["raw"] = (
        "".join("data: " + json.dumps(value) + "\n\n" for value in events) + "data: [DONE]\n\n"
    ).encode()
    observed = []
    result = gateway.submit(
        frozen(base),
        {"input": {"messages": [{"role": "user", "content": "hello"}]}},
        credentials(),
        on_text_delta=observed.append,
    )
    assert "".join(observed) == "safe/[redacted]/[redacted]/end"
    assert result.text == "".join(observed)


def test_standard_snapshot_cannot_receive_canvas_typed_secrets(gateway, provider):
    base, _, calls = provider
    with pytest.raises(GenerationError, match="invalid_credential"):
        gateway.submit(
            snapshot(base),
            {"input": {"messages": [{"role": "user", "content": "hello"}]}},
            credentials(),
        )
    assert calls == []


def test_headers_do_not_bypass_private_destination_guard(gateway, provider):
    base, _, calls = provider
    gateway.transport.settings.generation_allowed_hosts = []
    with pytest.raises(GenerationError, match="unsafe_address"):
        gateway.submit(
            frozen(base),
            {"input": {"messages": [{"role": "user", "content": "hello"}]}},
            credentials(),
        )
    assert calls == []


def test_same_origin_media_download_uses_frozen_headers_and_key(gateway, provider):
    base, state, calls = provider
    state["media"] = {"/result.mp4": (b"media", "video/mp4")}
    data, mime = gateway.download_media(
        base + "/result.mp4", 4096, snapshot=frozen(base), credential=credentials()
    )
    assert data == b"media" and mime == "video/mp4"
    assert calls[0][2]["X-Waf-Token"] == "private-header-token"
    assert calls[0][2]["Authorization"] == "Bearer private-main-key"


def test_cross_origin_media_download_is_anonymous_even_with_frozen_secrets(gateway, provider):
    base, state, calls = provider
    state["media"] = {"/result.mp4": (b"media", "video/mp4")}
    gateway.download_media(
        base + "/result.mp4",
        4096,
        snapshot=frozen("http://other.example:8000"),
        credential=credentials(),
    )
    assert "Authorization" not in calls[0][2]
    assert "X-Waf-Token" not in calls[0][2]


def test_authenticated_media_redirect_never_leaks_credentials_to_another_origin(gateway, provider):
    base, state, calls = provider
    state.update(
        status=302, headers={"Location": base.replace("127.0.0.1", "localhost") + "/stolen"}
    )
    with pytest.raises(GenerationError, match="provider_redirect"):
        gateway.download_media(
            base + "/result.mp4", 4096, snapshot=frozen(base), credential=credentials()
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "adapter,kind,model,path",
    [
        ("openai_chat.v1", "text", "gpt-4.1", "/v1/chat/completions"),
        ("openai_responses.v1", "text", "gpt-4.1", "/v1/responses"),
        ("openai_images.v1", "image", "gpt-image-2", "/v1/images/generations"),
        ("openai_speech.v1", "audio", "gpt-4o-mini-tts", "/v1/audio/speech"),
        ("ark_images.v1", "image", "doubao-seedream-5-0", "/api/v3/images/generations"),
        ("ark_video.v1", "video", "doubao-seedance-1-0-pro", "/api/v3/contents/generations/tasks"),
        (
            "dashscope_images.v1",
            "image",
            "qwen-image-3.0-pro",
            "/api/v1/services/aigc/image-generation/generation",
        ),
        (
            "dashscope_video.v1",
            "video",
            "wan2.6-t2v",
            "/api/v1/services/aigc/video-generation/video-synthesis",
        ),
    ],
)
def test_all_eight_implemented_adapters_send_frozen_channel_headers_over_real_http(
    gateway, provider, adapter, kind, model, path
):
    base, state, calls = provider
    if adapter == "openai_chat.v1":
        state["body"] = {
            "choices": [{"message": {"content": "safe text"}, "finish_reason": "stop"}]
        }
    elif adapter == "openai_responses.v1":
        state["body"] = {
            "id": "response-1",
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "safe text"}]}
            ],
        }
    elif adapter in {"openai_images.v1", "ark_images.v1"}:
        state["body"] = {"data": [{"b64_json": "aW1hZ2U="}]}
    elif adapter == "openai_speech.v1":
        state.update(raw=b"ID3fixture-audio", headers={"Content-Type": "audio/mpeg"})
    elif adapter == "ark_video.v1":
        state["body"] = {"id": "job-original"}
    else:
        state["body"] = {"output": {"task_id": "job-original", "task_status": "PENDING"}}
    payload = frozen_request(
        request(kind, {"audioVoice": "alloy"} if kind == "audio" else {}), adapter
    )
    result = gateway.submit(frozen(base, kind, model), payload, credentials(), adapter)
    assert result.status in {"succeeded", "submitted"}
    assert len(calls) == 1 and calls[0][:2] == ("POST", path)
    assert calls[0][2]["X-Waf-Token"] == "private-header-token"
    assert calls[0][2]["Authorization"] == "Bearer private-main-key"
    assert "private-header-token" not in json.dumps(calls[0][3])
    assert "private-main-key" not in json.dumps(calls[0][3])


def test_source_utf8_header_value_is_sent_as_original_bytes(gateway, provider):
    base, state, calls = provider
    state["body"] = {"choices": [{"message": {"content": "safe text"}, "finish_reason": "stop"}]}
    gateway.submit(
        frozen(base),
        {"input": {"messages": [{"role": "user", "content": "hello"}]}},
        CanvasCredentials(
            apiKey="private-main-key", headers=[{"name": "X-Waf-Token", "value": "私密值"}]
        ),
    )
    assert calls[0][2]["X-Waf-Token"].encode("latin-1") == "私密值".encode()


def test_real_second_origin_get_receives_no_provider_or_waf_secret(gateway, provider):
    base, _, first_calls = provider
    received = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()
            self.wfile.write(b"media")

        def log_message(self, *_):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    thread = threading.Thread(target=target.serve_forever, daemon=True)
    thread.start()
    try:
        data, _ = gateway.download_media(
            f"http://127.0.0.1:{target.server_port}/media",
            4096,
            snapshot=frozen(base),
            credential=credentials(),
        )
    finally:
        target.shutdown()
        target.server_close()
        thread.join()
    assert data == b"media" and len(received) == 1 and first_calls == []
    assert "Authorization" not in received[0] and "X-Waf-Token" not in received[0]


@pytest.mark.parametrize(
    "target",
    ["file:///private", "http://user:password@127.0.0.1/video", "http://127.0.0.1/video#fragment"],
)
def test_authenticated_media_rejects_invalid_target_before_http(gateway, provider, target):
    base, _, calls = provider
    with pytest.raises(GenerationError, match="unsafe_address"):
        gateway.download_media(target, 4096, snapshot=frozen(base), credential=credentials())
    assert calls == []
