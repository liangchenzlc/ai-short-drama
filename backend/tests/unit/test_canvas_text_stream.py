"""供应商真实增量边界、断流及跨分块凭据脱敏。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from short_drama.ai.streaming import TextStreamObserver
from short_drama.ai.types import GenerationError


def event(value):
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()


def chat(value):
    return event({"choices": [{"index": 0, "delta": {"content": value}}]})


def test_supplier_delta_arrives_before_the_terminal_response():
    values = []
    observer = TextStreamObserver(values.append, responses=False)
    first = chat("你好")
    split = first.index("你".encode()) + 1
    observer.feed(first[:split])
    assert values == []
    observer.feed(first[split:-1])
    assert values == []
    observer.feed(first[-1:])
    assert values == ["你好"]
    observer.feed(chat("，世界"))
    assert values == ["你好", "，世界"]
    observer.finish()


def test_responses_stream_ignores_reasoning_and_never_duplicates_final_text():
    values = []
    observer = TextStreamObserver(values.append, responses=True)
    observer.feed(event({"type": "response.reasoning_text.delta", "delta": "私有思考"}))
    observer.feed(event({"type": "response.output_text.delta", "delta": "正文"}))
    observer.feed(event({"type": "response.completed", "response": {"output": []}}))
    observer.finish()
    assert values == ["正文"]


def test_secret_split_across_supplier_events_is_redacted_before_persistence():
    values = []
    observer = TextStreamObserver(values.append, responses=False, secret="sk-private-key")
    for value in ("正文sk-pr", "ivate-", "key结尾"):
        observer.feed(chat(value))
        assert "sk-private-key" not in "".join(values)
    observer.finish()
    assert "".join(values) == "正文[redacted]结尾"


@pytest.mark.parametrize("payload", [b"data: {bad}\n\n", b"data: \xff\n\n"])
def test_invalid_stream_remains_an_unknown_submission(payload):
    observer = TextStreamObserver(lambda _value: None, responses=False)
    with pytest.raises(GenerationError) as caught:
        observer.feed(payload)
    assert caught.value.accepted_unknown


def test_callback_failure_is_not_swallowed_or_resubmitted():
    def failed(_value):
        raise RuntimeError("controlled archive failure")

    observer = TextStreamObserver(failed, responses=False)
    with pytest.raises(RuntimeError, match="archive failure"):
        observer.feed(chat("已受理"))


@pytest.mark.parametrize("disconnect", [False, True])
def test_real_http_delivers_supplier_delta_before_completion_without_a_second_post(disconnect):
    from short_drama.ai.gateway import GenerationGateway

    observed = threading.Event()
    values, calls = [], []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(chat("真实增量"))
            self.wfile.flush()
            if not observed.wait(5) or disconnect:
                return
            self.wfile.write(chat("，完成"))
            self.wfile.write(event({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()

    def delta(content):
        values.append(content)
        observed.set()

    gateway = GenerationGateway(SimpleNamespace(generation_allowed_hosts=["127.0.0.1"]))
    snapshot = {
        "base_url": f"http://127.0.0.1:{provider.server_port}/v1",
        "provider": "openai",
        "model_key": "gpt-4o-mini",
        "service_type": "text",
        "budget_seconds": 10,
    }
    try:
        if disconnect:
            with pytest.raises(GenerationError) as caught:
                gateway.submit(
                    snapshot,
                    {"input": {"messages": [{"role": "user", "content": "测试"}]}},
                    "controlled-test-key",
                    on_text_delta=delta,
                )
            assert caught.value.accepted_unknown
            assert values == ["真实增量"]
        else:
            result = gateway.submit(
                snapshot,
                {"input": {"messages": [{"role": "user", "content": "测试"}]}},
                "controlled-test-key",
                on_text_delta=delta,
            )
            assert result.text == "真实增量，完成" == "".join(values)
        assert observed.is_set() and len(calls) == 1
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
