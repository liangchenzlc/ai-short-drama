"""Provider contracts through real local HTTP; these are not live provider tests."""

import importlib.util
import json
import threading
import time
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace

import pytest


def test_generation_gateway_is_available():
    assert importlib.util.find_spec("short_drama.ai") is not None


def test_text_stream_collects_chunks_usage_and_completion(gateway, provider):
    base, state, calls = provider
    state["headers"] = {"Content-Type": "text/event-stream; charset=utf-8"}
    frames = [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "世界"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"total_tokens": 12}},
    ]
    state["raw"] = (
        ": heartbeat\r\n\r\n"
        + "".join("data: " + json.dumps(x, ensure_ascii=False) + "\r\n\r\n" for x in frames)
        + "data: [DONE]\r\n\r\n"
    ).encode()
    result = gateway.submit(
        snapshot(base), {"input": {"messages": [{"role": "user", "content": "hi"}]}}, "test-secret"
    )
    assert calls[0][3]["stream"] is True
    assert result.text == "你好世界" and result.finish_reason == "stop"
    assert result.usage == {"total_tokens": 12}
    assert len(calls) == 1


@pytest.mark.parametrize(
    "ending", ["", "data: [DONE]\n\n", 'data: {"error":{"message":"private"}}\n\n']
)
def test_incomplete_stream_is_unknown_and_never_retried(gateway, provider, ending):
    from short_drama.ai import GenerationError

    base, state, calls = provider
    state["headers"] = {"Content-Type": "text/event-stream"}
    state["raw"] = (
        'data: {"choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
        + ending
    ).encode()
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base),
            {"input": {"messages": [{"role": "user", "content": "hi"}]}},
            "test-secret",
        )
    assert error.value.accepted_unknown
    assert "private" not in str(error.value)
    assert len(calls) == 1


def test_responses_stream_uses_terminal_response(gateway, provider):
    base, state, calls = provider
    state["headers"] = {"Content-Type": "text/event-stream"}
    response = {
        "id": "resp-1",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
        "usage": {"output_tokens": 1},
    }
    state["raw"] = (
        "event: response.completed\ndata: "
        + json.dumps({"type": "response.completed", "response": response})
        + "\n\n"
    ).encode()
    result = gateway.submit(
        snapshot(base + "/v1/responses"),
        {"input": {"messages": [{"role": "user", "content": "hi"}]}},
        "test-secret",
    )
    assert calls[0][3]["stream"] is True
    assert result.text == "OK" and result.usage == {"output_tokens": 1}


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_upstream_http_status_is_retained_without_response_body(gateway, provider, status):
    from short_drama.ai import GenerationError

    base, state, calls = provider
    state.update(status=status, raw=b"private-provider-error")
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base),
            {"input": {"messages": [{"role": "user", "content": "hi"}]}},
            "test-secret",
        )
    assert error.value.http_status == status
    assert error.value.accepted_unknown
    assert "private-provider-error" not in str(error.value)
    assert len(calls) == 1


@pytest.fixture
def provider():
    state = {"status": 200, "body": {}, "headers": {}}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.headers.get_content_type() == "multipart/form-data":
                message = BytesParser(policy=policy.default).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body
                )
                parsed = [
                    (
                        part.get_param("name", header="content-disposition"),
                        part.get_filename(),
                        part.get_content_type(),
                        part.get_payload(decode=True),
                    )
                    for part in message.iter_parts()
                ]
            else:
                parsed = json.loads(body) if body else None
            calls.append((self.command, self.path, dict(self.headers), parsed))
            if self.path in state.get("media", {}):
                data, mime = state["media"][self.path]
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if state.get("disconnect"):
                self.connection.close()
                return
            if state.get("delay"):
                time.sleep(state["delay"])
            data = state.get("raw", json.dumps(state["body"]).encode())
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(data)))
            for key, value in state["headers"].items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        do_POST = handle_request
        do_GET = handle_request

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def gateway():
    from short_drama.ai import GenerationGateway

    return GenerationGateway(SimpleNamespace(generation_allowed_hosts=["127.0.0.1"]))


def snapshot(base, kind="text", model="model"):
    return {"base_url": base, "service_type": kind, "model_key": model, "budget_seconds": 5}


@pytest.mark.parametrize(
    "base,kind,model,expected",
    [
        ("https://api.deepseek.com", "text", "deepseek-chat", "openai_chat.v1"),
        ("https://ark.cn-beijing.volces.com/api/v3", "text", "doubao", "openai_chat.v1"),
        ("https://api.openai.com/v1/responses", "text", "gpt", "openai_responses.v1"),
        ("https://api.openai.com/v1", "image", "gpt-image-2", "openai_images.v1"),
        ("https://ark.cn-beijing.volces.com/api/v3", "image", "seedream", "ark_images.v1"),
        ("https://ark.cn-beijing.volces.com/api/v3", "video", "seedance", "ark_video.v1"),
        ("https://dashscope.aliyuncs.com", "image", "wan2.6-t2i", "dashscope_images.v1"),
        ("https://dashscope.aliyuncs.com", "video", "wan2.2-t2v-plus", "dashscope_video.v1"),
    ],
)
def test_adapter_selection_has_no_paid_probe(base, kind, model, expected):
    from short_drama.ai import select_adapter

    assert select_adapter(snapshot(base, kind, model)) == expected


def test_unknown_video_protocol_rejected():
    from short_drama.ai import GenerationError, select_adapter

    with pytest.raises(GenerationError) as error:
        select_adapter(snapshot("https://example.com/v1", "video"))
    assert error.value.code == "unsupported_protocol"
    assert not error.value.accepted_unknown


def test_chat_request_and_truncation_keep_text(gateway, provider):
    base, state, calls = provider
    state["body"] = {
        "choices": [{"message": {"content": "scene one"}, "finish_reason": "length"}],
        "usage": {"total_tokens": 42},
    }
    result = gateway.submit(
        snapshot(base + "/v1"),
        {
            "input": {"messages": [{"role": "user", "content": "write"}]},
            "parameters": {"temperature": 0.3, "max_output_tokens": 123},
        },
        "test-secret",
        "openai_chat.v1",
    )
    assert calls[0][1] == "/v1/chat/completions"
    assert calls[0][3] == {
        "model": "model",
        "messages": [{"role": "user", "content": "write"}],
        "temperature": 0.3,
        "max_tokens": 123,
        "stream": True,
    }
    assert result.text == "scene one" and result.finish_reason == "length"
    assert result.usage == {"total_tokens": 42}


def test_responses_request_and_output(gateway, provider):
    base, state, calls = provider
    state["body"] = {
        "id": "resp-1",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}],
        "usage": {"output_tokens": 2},
    }
    result = gateway.submit(
        snapshot(base + "/v1/responses"),
        {
            "input": {"messages": [{"role": "user", "content": "hello"}]},
            "parameters": {"max_output_tokens": 50},
        },
        "test-secret",
        "openai_responses.v1",
    )
    assert calls[0][1] == "/v1/responses"
    assert calls[0][3]["input"] == [{"role": "user", "content": "hello"}]
    assert calls[0][3]["max_output_tokens"] == 50
    assert result.text == "hello" and result.status == "succeeded"


def test_openai_images_preserves_count_and_base64(gateway, provider):
    base, state, calls = provider
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}], "usage": {"total_tokens": 8}}
    result = gateway.submit(
        snapshot(base + "/v1", "image", "gpt-image-2"),
        {
            "input": {"prompt": "scene"},
            "parameters": {"count": 1, "aspect": "1:1", "resolution": "1024x1024"},
        },
        "test-secret",
        "openai_images.v1",
    )
    assert calls[0][1] == "/v1/images/generations"
    assert calls[0][3] == {"model": "gpt-image-2", "prompt": "scene", "n": 1, "size": "1024x1024"}
    assert result.outputs[0] == {"base64": "aW1hZ2U=", "media_type": "image", "mime": "image/png"}


def reference_image(format="PNG", color="red"):
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize("base_path", ["", "/v1", "/v1/images/generations", "/v1/images/edits"])
def test_gpt_image_edits_upload_all_references_without_leaking_credentials(
    gateway, provider, base_path
):
    base, state, calls = provider
    png, jpeg = reference_image(), reference_image("JPEG", "blue")
    state["media"] = {
        "/character?signature=abc": (png, "application/octet-stream"),
        "/scene": (jpeg, "image/jpeg"),
    }
    state["body"] = {"data": [{"b64_json": "aW1hZ2U="}, {"url": "https://cdn.example/b.png"}]}
    request = {
        "input": {"prompt": "场景参考", "reference_urls": [base + path for path in state["media"]]},
        "parameters": {"count": 2, "aspect": "16:9", "resolution": "2K"},
    }
    result = gateway.submit(
        snapshot(base + base_path, "image", "gpt-image-2.5-flare"), request, "test-secret"
    )
    assert [(call[0], call[1]) for call in calls] == [
        ("GET", "/character?signature=abc"),
        ("GET", "/scene"),
        ("POST", "/v1/images/edits"),
    ]
    assert all("Authorization" not in call[2] for call in calls[:2])
    assert calls[2][2]["Authorization"] == "Bearer test-secret"
    fields = calls[2][3]
    assert {name: data.decode() for name, filename, _, data in fields if filename is None} == {
        "model": "gpt-image-2.5-flare",
        "prompt": "场景参考",
        "n": "2",
        "size": "2560x1440",
    }
    assert [(name, mime, data) for name, filename, mime, data in fields if filename] == [
        ("image[]", "image/png", png),
        ("image[]", "image/jpeg", jpeg),
    ]
    assert result.status == "succeeded" and len(result.outputs) == 2
    assert result.resolved_parameters == {"n": 2, "size": "2560x1440"}
    assert "image" not in request["input"]


@pytest.mark.parametrize(
    "model", ["gpt-image-1", "gpt-image-1.5", "gpt-image-2", "gpt-image-2.5-flare"]
)
def test_gpt_image_reference_admission_is_pure_and_matches_capabilities(model):
    from short_drama.ai import capabilities, validate_request

    snap = snapshot("https://provider.example/v1", "image", model)
    request = {"input": {"prompt": "scene", "reference_media_ids": ["1", "2"]}}
    assert capabilities(snap)["reference_images"] is True
    assert validate_request(snap, request)["resolved_parameters"] == {"n": 1}
    assert request == {"input": {"prompt": "scene", "reference_media_ids": ["1", "2"]}}


@pytest.mark.parametrize("model,count", [("dall-e-3", 1), ("unknown", 1), ("gpt-image-2", 17)])
def test_unsupported_image_edits_are_rejected_before_any_http(gateway, provider, model, count):
    from short_drama.ai import GenerationError, capabilities

    base, _, calls = provider
    snap = snapshot(base, "image", model)
    with pytest.raises(GenerationError, match="unsupported_parameters"):
        gateway.submit(
            snap, {"input": {"prompt": "hi", "reference_urls": [base + "/ref"] * count}}, "secret"
        )
    assert not calls
    if model != "gpt-image-2":
        assert not capabilities(snap)["reference_images"]


@pytest.mark.parametrize("data", [b"not an image", reference_image("GIF")])
def test_invalid_reference_file_never_submits_generation(gateway, provider, data):
    from short_drama.ai import GenerationError

    base, state, calls = provider
    state["media"] = {"/ref": (data, "image/png")}
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base, "image", "gpt-image-2"),
            {"input": {"prompt": "scene", "reference_urls": [base + "/ref"]}},
            "secret",
        )
    assert error.value.code == "invalid_reference_image"
    assert not error.value.accepted_unknown
    assert [call[0] for call in calls] == ["GET"]


@pytest.mark.parametrize(
    "status,code,unknown", [(404, "provider_endpoint", False), (503, "upstream_unavailable", True)]
)
def test_edit_error_never_falls_back_to_text_to_image(gateway, provider, status, code, unknown):
    from short_drama.ai import GenerationError

    base, state, calls = provider
    state.update(media={"/ref": (reference_image(), "image/png")}, status=status)
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base, "image", "gpt-image-2"),
            {"input": {"prompt": "scene", "reference_urls": [base + "/ref"]}},
            "secret",
        )
    assert error.value.code == code and error.value.accepted_unknown is unknown
    assert [(call[0], call[1]) for call in calls] == [("GET", "/ref"), ("POST", "/v1/images/edits")]


def test_edit_reference_download_blocks_private_destinations(provider):
    from short_drama.ai import GenerationError, GenerationGateway

    base, _, calls = provider
    gateway = GenerationGateway(SimpleNamespace())
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot("https://provider.example/v1", "image", "gpt-image-2"),
            {"input": {"prompt": "scene", "reference_urls": [base + "/ref"]}},
            "secret",
        )
    assert error.value.code == "unsafe_address" and not error.value.accepted_unknown
    assert not calls


@pytest.mark.parametrize("per_file,total", [(1, 1024), (1024, 100)])
def test_edit_downloads_enforce_file_and_total_limits(
    gateway, provider, monkeypatch, per_file, total
):
    from short_drama.ai import GenerationError

    monkeypatch.setattr("short_drama.ai.gateway.MAX_REFERENCE_IMAGE_BYTES", per_file)
    monkeypatch.setattr("short_drama.ai.gateway.MAX_REFERENCE_TOTAL_BYTES", total)
    base, state, calls = provider
    data = reference_image()
    assert len(data) < 100 < 2 * len(data)
    state["media"] = {"/a": (data, "image/png"), "/b": (data, "image/png")}
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base, "image", "gpt-image-2"),
            {"input": {"prompt": "scene", "reference_urls": [base + "/a", base + "/b"]}},
            "secret",
        )
    assert error.value.code == "response_too_large" and not error.value.accepted_unknown
    assert all(call[0] == "GET" for call in calls)


def test_edit_downloads_and_post_share_one_deadline(gateway, provider, monkeypatch):
    base, state, _ = provider
    state.update(
        media={"/ref": (reference_image("WEBP"), "image/webp")},
        body={"data": [{"b64_json": "aW1hZ2U="}]},
    )
    deadlines = []
    original = gateway.transport.request

    def request(*args, **kwargs):
        deadlines.append(kwargs["deadline"])
        return original(*args, **kwargs)

    monkeypatch.setattr(gateway.transport, "request", request)
    gateway.submit(
        snapshot(base, "image", "gpt-image-2"),
        {"input": {"prompt": "scene", "reference_urls": [base + "/ref"]}},
        "secret",
    )
    assert len(deadlines) == 2 and deadlines[0] == deadlines[1]


@pytest.mark.parametrize(
    "aspect,size", [("16:9", "2560x1440"), ("9:16", "1440x2560"), ("1:1", "2048x2048")]
)
def test_gpt_image_2_default_storyboard_resolution_preserves_aspect(aspect, size):
    from short_drama.ai import GenerationError, validate_request

    request = {
        "input": {"prompt": "scene", "reference_media_ids": ["1"]},
        "parameters": {"aspect": aspect, "resolution": "2K"},
    }
    value = validate_request(
        snapshot("https://provider.example/v1", "image", "gpt-image-2.5-flare"), request
    )
    assert value["resolved_parameters"] == {"n": 1, "size": size}
    with pytest.raises(GenerationError) as error:
        validate_request(snapshot("https://provider.example/v1", "image", "gpt-image-1"), request)
    assert error.value.code == "unsupported_image_size"


def test_ark_image_reference_and_sequential_images(gateway, provider):
    base, state, calls = provider
    references = [
        "https://cdn.example/character.png",
        "https://cdn.example/scene.png",
        "https://cdn.example/prop.png",
    ]
    state["body"] = {
        "data": [{"url": "https://cdn.example/a.png"}, {"url": "https://cdn.example/b.png"}]
    }
    result = gateway.submit(
        snapshot(base + "/api/v3", "image", "doubao-seedream-4-5"),
        {
            "input": {"prompt": "scene", "reference_urls": references},
            "parameters": {"count": 2, "resolution": "2K"},
        },
        "test-secret",
        "ark_images.v1",
    )
    assert calls[0][1] == "/api/v3/images/generations"
    assert calls[0][3]["image"] == references
    assert len(calls) == 1
    assert calls[0][3]["sequential_image_generation_options"] == {"max_images": 2}
    assert len(result.outputs) == 2


def test_ark_video_submit_and_poll_use_distinct_methods(gateway, provider):
    base, state, calls = provider
    snap = snapshot(base + "/api/v3", "video", "doubao-seedance")
    state["body"] = {"id": "task-1"}
    result = gateway.submit(
        snap,
        {
            "input": {
                "prompt": "pan",
                "first_frame_url": "https://cdn.example/first.png",
                "last_frame_url": "https://cdn.example/last.png",
            },
            "parameters": {"aspect": "16:9", "resolution": "1080p", "duration_ms": 6000},
        },
        "test-secret",
        "ark_video.v1",
    )
    assert result.status == "submitted" and result.provider_task_id == "task-1"
    assert calls[0][1] == "/api/v3/contents/generations/tasks"
    assert calls[0][3]["duration"] == 6 and calls[0][3]["ratio"] == "16:9"
    assert calls[0][3]["content"][2]["role"] == "last_frame"
    state["body"] = {
        "id": "task-1",
        "status": "succeeded",
        "content": {"video_url": "https://cdn.example/video.mp4"},
        "usage": {"total_tokens": 100},
    }
    result = gateway.poll(snap, "task-1", "test-secret", "ark_video.v1")
    assert calls[1][0:2] == ("GET", "/api/v3/contents/generations/tasks/task-1")
    assert result.outputs == [{"url": "https://cdn.example/video.mp4", "media_type": "video"}]


def test_dashscope_legacy_images_submit_then_poll(gateway, provider):
    base, state, calls = provider
    snap = snapshot(base, "image", "wan2.2-t2i-flash")
    state["body"] = {
        "output": {"task_id": "task-2", "task_status": "PENDING"},
        "request_id": "req-2",
    }
    result = gateway.submit(
        snap,
        {"input": {"prompt": "scene"}, "parameters": {"count": 2, "resolution": "1024x1024"}},
        "test-secret",
        "dashscope_images.v1",
    )
    assert result.status == "submitted"
    assert calls[0][1] == "/api/v1/services/aigc/text2image/image-synthesis"
    assert calls[0][2]["X-DashScope-Async"] == "enable"
    assert calls[0][3]["parameters"] == {"n": 2, "size": "1024*1024"}
    state["body"] = {
        "output": {
            "task_id": "task-2",
            "task_status": "SUCCEEDED",
            "results": [{"url": "https://cdn.example/a.png"}],
        }
    }
    assert (
        gateway.poll(snap, "task-2", "test-secret", "dashscope_images.v1").outputs[0]["media_type"]
        == "image"
    )
    assert calls[1][1] == "/api/v1/tasks/task-2"


def test_dashscope_modern_images_messages_contract(gateway, provider):
    base, state, calls = provider
    state["body"] = {
        "output": {"choices": [{"message": {"content": [{"image": "https://cdn.example/a.png"}]}}]}
    }
    result = gateway.submit(
        snapshot(base, "image", "wan2.6-t2i"),
        {"input": {"prompt": "scene"}, "parameters": {"count": 1}},
        "test-secret",
        "dashscope_images.v1",
    )
    assert calls[0][1] == "/api/v1/services/aigc/multimodal-generation/generation"
    assert calls[0][3]["input"] == {"messages": [{"role": "user", "content": [{"text": "scene"}]}]}
    assert calls[0][3]["parameters"]["n"] == 1
    assert result.status == "succeeded"


def test_dashscope_video_submit_poll(gateway, provider):
    base, state, calls = provider
    snap = snapshot(base, "video", "wan2.2-i2v-flash")
    state["body"] = {"output": {"task_id": "task-3", "task_status": "PENDING"}}
    gateway.submit(
        snap,
        {
            "input": {"prompt": "pan", "first_frame_url": "https://cdn.example/ref.png"},
            "parameters": {"resolution": "720p", "duration_ms": 5000},
        },
        "test-secret",
        "dashscope_video.v1",
    )
    assert calls[0][3]["input"]["img_url"] == "https://cdn.example/ref.png"
    assert calls[0][3]["parameters"] == {"resolution": "720P", "duration": 5}
    state["body"] = {
        "output": {
            "task_id": "task-3",
            "task_status": "SUCCEEDED",
            "video_url": "https://cdn.example/v.mp4",
        }
    }
    assert (
        gateway.poll(snap, "task-3", "test-secret", "dashscope_video.v1").outputs[0]["media_type"]
        == "video"
    )


@pytest.mark.parametrize(
    "state_update,code,unknown,mismatch",
    [
        ({"disconnect": True}, "transport_error", True, False),
        ({"status": 503}, "upstream_unavailable", True, False),
        ({"status": 404}, "provider_endpoint", False, False),
        ({"raw": b"not json"}, "invalid_response", True, False),
        ({"body": {"unexpected": "test-secret"}}, "invalid_response", True, False),
    ],
)
def test_submit_never_retries_or_echoes_errors(
    gateway, provider, state_update, code, unknown, mismatch
):
    from short_drama.ai import GenerationError

    base, state, calls = provider
    state.update(state_update)
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base),
            {"input": {"messages": [{"role": "user", "content": "hi"}]}},
            "test-secret",
            "openai_chat.v1",
        )
    assert error.value.code == code
    assert error.value.accepted_unknown is unknown
    assert error.value.protocol_mismatch is mismatch
    assert len(calls) == 1 and "test-secret" not in str(error.value)


def test_unsupported_parameter_rejected_before_post(gateway, provider):
    from short_drama.ai import GenerationError

    base, _, calls = provider
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base, "image"),
            {"input": {"prompt": "hi"}, "parameters": {"invented": True}},
            "test-secret",
            "openai_images.v1",
        )
    assert error.value.code == "unsupported_parameters" and calls == []


def test_private_destinations_blocked_before_credential_sent(provider):
    from short_drama.ai import GenerationError, GenerationGateway

    base, _, calls = provider
    with pytest.raises(GenerationError) as error:
        GenerationGateway(SimpleNamespace()).submit(
            snapshot(base),
            {"input": {"messages": [{"role": "user", "content": "hi"}]}},
            "test-secret",
            "openai_chat.v1",
        )
    assert error.value.code == "unsafe_address" and calls == []


def test_download_has_no_authorization_and_retains_signed_query(gateway, provider):
    base, state, calls = provider
    state.update(raw=b"image", headers={"Content-Type": "image/png"})
    assert gateway.download_media(base + "/image?signature=abc", 10) == (b"image", "image/png")
    assert calls[0][1] == "/image?signature=abc"
    assert "Authorization" not in calls[0][2]


def test_download_rechecks_redirect_destination(gateway, provider):
    from short_drama.ai import GenerationError

    base, state, calls = provider
    state.update(status=302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
    with pytest.raises(GenerationError) as error:
        gateway.download_media(base + "/image", 10)
    assert error.value.code == "unsafe_address" and len(calls) == 1


def test_download_enforces_byte_limit(gateway, provider):
    from short_drama.ai import GenerationError

    base, state, _ = provider
    state["raw"] = b"01234567890"
    with pytest.raises(GenerationError) as error:
        gateway.download_media(base + "/image", 10)
    assert error.value.code == "response_too_large"


def test_admission_validates_media_ids_without_resolving_or_mutating():
    from short_drama.ai import validate_request

    request = {
        "input": {"prompt": "scene", "reference_media_ids": ["123"]},
        "parameters": {"count": 2},
    }
    result = validate_request(
        snapshot("https://ark.cn-beijing.volces.com/api/v3", "image", "seedream"), request
    )
    assert result["adapter"] == "ark_images.v1"
    assert result["resolved_parameters"]["sequential_image_generation_options"] == {"max_images": 2}
    assert request["input"] == {"prompt": "scene", "reference_media_ids": ["123"]}


def test_capabilities_reject_unknown_video_and_do_not_claim_verified_model_support():
    from short_drama.ai import capabilities

    value = capabilities(snapshot("https://unknown.example/v1", "video"))
    assert value["known"] is False and value["parameters"] == []
    value = capabilities(snapshot("https://ark.cn-beijing.volces.com/api/v3", "video", "seedance"))
    assert value["known"] is True and value["last_frame"] is True


def test_unknown_dashscope_task_outcome_is_not_a_confirmed_failure(gateway, provider):
    from short_drama.ai import GenerationError

    base, state, _ = provider
    state["body"] = {"output": {"task_id": "task-3", "task_status": "UNKNOWN"}}
    with pytest.raises(GenerationError) as error:
        gateway.poll(snapshot(base, "video"), "task-3", "test-secret", "dashscope_video.v1")
    assert error.value.accepted_unknown is True


def test_poll_with_missing_status_is_invalid_not_pending(gateway, provider):
    from short_drama.ai import GenerationError

    base, state, _ = provider
    state["body"] = {"id": "task-3"}
    with pytest.raises(GenerationError) as error:
        gateway.poll(snapshot(base, "video"), "task-3", "test-secret", "ark_video.v1")
    assert error.value.code == "invalid_response"


def test_credential_echo_is_removed_from_text_and_usage(gateway, provider):
    base, state, _ = provider
    state["body"] = {
        "choices": [{"message": {"content": "test-secret"}, "finish_reason": "stop"}],
        "usage": {"metadata": "test-secret"},
    }
    result = gateway.submit(
        snapshot(base),
        {"input": {"messages": [{"role": "user", "content": "hi"}]}},
        "test-secret",
        "openai_chat.v1",
    )
    assert "test-secret" not in repr(result)


def test_json_url_with_credential_cannot_escape_to_manifest(gateway, provider):
    from short_drama.ai import GenerationError

    base, state, _ = provider
    state["body"] = {"data": [{"url": "https://example.com/test-secret"}]}
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base, "image"), {"input": {"prompt": "hi"}}, "test-secret", "openai_images.v1"
        )
    assert error.value.accepted_unknown


def test_video_duration_never_silently_rounds_milliseconds(gateway, provider):
    from short_drama.ai import GenerationError

    base, _, calls = provider
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base, "video"),
            {"input": {"prompt": "hi"}, "parameters": {"duration_ms": 5500}},
            "test-secret",
            "ark_video.v1",
        )
    assert error.value.code == "unsupported_parameters" and not calls


def test_provider_task_id_cannot_rewrite_poll_path(gateway, provider):
    from short_drama.ai import GenerationError

    base, _, calls = provider
    with pytest.raises(GenerationError) as error:
        gateway.poll(snapshot(base, "video"), "../other", "test-secret", "ark_video.v1")
    assert error.value.code == "invalid_provider_task" and not calls


def test_gateway_validation_is_pure_and_uses_frozen_adapter(gateway, provider):
    base, _, calls = provider
    params = gateway.validate(
        snapshot(base),
        {
            "input": {"messages": [{"role": "user", "content": "hi"}]},
            "parameters": {"max_output_tokens": 42},
        },
        "openai_responses.v1",
    )
    assert params == {"stream": True, "max_output_tokens": 42}
    assert not calls


def test_generation_error_accepts_internal_message_without_reflecting_it():
    from short_drama.ai import GenerationError

    error = GenerationError("reference_missing", "secret-provider-body")
    assert error.code == "reference_missing"
    assert "secret-provider-body" not in str(error)


def test_verified_cache_matches_credential_and_resolver_fingerprint():
    from short_drama.ai import capability_fingerprint, select_adapter

    snap = snapshot("https://custom.example/v1", "text")
    snap["credential_identity"] = "cipher-hash"
    snap["capability_cache"] = {
        "adapter": "openai_responses.v1",
        "fingerprint": capability_fingerprint(snap, "cipher-hash"),
    }
    assert select_adapter(snap) == "openai_responses.v1"
    snap["credential_identity"] = "rotated-cipher-hash"
    assert select_adapter(snap) == "openai_chat.v1"
    snap["credential_identity"] = "cipher-hash"
    snap["model_key"] = "other-model"
    assert select_adapter(snap) == "openai_chat.v1"


@pytest.mark.parametrize(
    "model,inputs,params",
    [
        ("wan2.6-t2i", {"prompt": "hi", "reference_media_ids": ["1"]}, {}),
        ("wan2.6-image", {"prompt": "hi"}, {}),
        ("qwen-image", {"prompt": "hi", "reference_media_ids": ["1"]}, {}),
    ],
)
def test_dashscope_image_inputs_follow_model_contract(model, inputs, params):
    from short_drama.ai import GenerationError, validate_request

    with pytest.raises(GenerationError) as error:
        validate_request(
            snapshot("https://dashscope.aliyuncs.com", "image", model),
            {"input": inputs, "parameters": params},
        )
    assert error.value.code == "unsupported_parameters"


def test_dashscope_image_edit_supports_documented_2k_tier():
    from short_drama.ai import validate_request

    value = validate_request(
        snapshot("https://dashscope.aliyuncs.com", "image", "wan2.6-image"),
        {
            "input": {"prompt": "hi", "reference_media_ids": ["1"]},
            "parameters": {"resolution": "2K", "count": 2},
        },
    )
    assert value["resolved_parameters"] == {"n": 2, "size": "2K", "enable_interleave": False}


def test_known_dashscope_i2v_resolution_is_rejected_before_submission():
    from short_drama.ai import GenerationError, validate_request

    with pytest.raises(GenerationError) as error:
        validate_request(
            snapshot("https://dashscope.aliyuncs.com", "video", "wan2.2-i2v-plus"),
            {
                "input": {"prompt": "hi", "first_frame_media_id": "1"},
                "parameters": {"resolution": "720p"},
            },
        )
    assert error.value.code == "unsupported_parameters"


def test_submit_read_timeout_is_unknown_without_second_post(gateway, provider):
    from short_drama.ai import GenerationError

    base, state, calls = provider
    state["delay"] = 0.15
    snap = snapshot(base)
    snap["budget_seconds"] = 0.03
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snap,
            {"input": {"messages": [{"role": "user", "content": "hi"}]}},
            "test-secret",
            "openai_chat.v1",
        )
    assert error.value.code == "timeout" and error.value.accepted_unknown
    assert len(calls) == 1


def test_generation_redirect_does_not_forward_authorization(gateway, provider):
    from short_drama.ai import GenerationError

    base, state, calls = provider
    state.update(status=302, headers={"Location": base + "/stolen"})
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base),
            {"input": {"messages": [{"role": "user", "content": "hi"}]}},
            "test-secret",
            "openai_chat.v1",
        )
    assert error.value.code == "provider_redirect" and len(calls) == 1


def test_output_manifest_cannot_exceed_application_limit(gateway, provider):
    from short_drama.ai import GenerationError

    base, state, _ = provider
    state["body"] = {"data": [{"url": "https://cdn.example/image.png"}] * 5}
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(base, "image"), {"input": {"prompt": "hi"}}, "test-secret", "openai_images.v1"
        )
    assert error.value.code == "invalid_response" and error.value.accepted_unknown


def test_https_pins_validated_ip_and_verifies_original_hostname(monkeypatch):
    from short_drama.ai import GenerationGateway, transport

    connections = []
    resolutions = []
    data = json.dumps(
        {"choices": [{"message": {"content": "safe"}, "finish_reason": "stop"}]}
    ).encode()

    def resolve(host, port, **kwargs):
        resolutions.append((host, port))
        return [(2, 1, 6, "", ("8.8.8.8", 443))]

    class Response:
        status = 200
        headers = {}

        def __init__(self):
            self.data = data

        def read1(self, *_args, **_kwargs):
            value, self.data = self.data, b""
            return value

        def close(self):
            pass

        release_conn = close

    class Pool:
        def __init__(self, **kwargs):
            connections.append(kwargs)

        def request(self, method, path, **kwargs):
            assert method == "POST" and path == "/v1/chat/completions"
            assert kwargs["retries"] is False and kwargs["redirect"] is False
            assert kwargs["headers"]["Host"] == "provider.example"
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(transport.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(transport.urllib3, "HTTPSConnectionPool", Pool)
    result = GenerationGateway(SimpleNamespace()).submit(
        snapshot("https://provider.example/v1"),
        {"input": {"messages": [{"role": "user", "content": "hi"}]}},
        "test-secret",
    )
    assert result.text == "safe"
    assert resolutions == [("provider.example", 443)]
    assert connections[0]["host"] == "8.8.8.8"
    assert (
        connections[0]["server_hostname"] == connections[0]["assert_hostname"] == "provider.example"
    )
    assert connections[0]["cert_reqs"] == "CERT_REQUIRED"
