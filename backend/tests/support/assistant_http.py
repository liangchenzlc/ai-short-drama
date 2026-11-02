"""Local HTTP wire fixtures shared by the production model factory tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def provider():
    state = {"responses": [], "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["calls"].append((self.path, dict(self.headers), json.loads(raw)))
            if not state["responses"]:
                self.connection.shutdown(2)
                return
            reply = state["responses"].pop(0)
            status, body = reply[:2]
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", reply[2] if len(reply) > 2 else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            gate = state.get("stream_gate")
            if gate is None:
                self.wfile.write(raw)
            else:
                first, remaining = raw.split(b"\n\n", 1)
                self.wfile.write(first + b"\n\n")
                self.wfile.flush()
                state["gate_released"] = gate.wait(timeout=2)
                self.wfile.write(remaining)
                state["stream_complete"] = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def snapshot(base, protocol):
    return {
        "service_type": "text",
        "base_url": base + ("/responses" if protocol == "responses" else "/chat/completions"),
        "model_key": "test-model",
    }


def response(protocol, *, text=None, calls=None):
    usage = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    if protocol == "chat":
        message = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for call_id, name, args in calls
            ]
        return {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }
    output = (
        [
            {
                "type": "function_call",
                "id": f"fc_{call_id}",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(args),
                "status": "completed",
            }
            for call_id, name, args in calls
        ]
        if calls
        else [
            {
                "type": "message",
                "id": "msg_local",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ]
    )
    return {
        "id": "resp_local",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "test-model",
        "output": output,
        "usage": usage,
    }


def sse_response(protocol, *, text=None, calls=None, with_usage=True, terminal=True):
    events = []
    if protocol == "chat":

        def chunk(delta, finish=None):
            return {
                "id": "chatcmpl-local",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        if calls:
            for index, (call_id, name, args) in enumerate(calls):
                events.append(
                    chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": call_id,
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)},
                                }
                            ]
                        }
                    )
                )
        else:
            events.extend(chunk({"content": part}) for part in (text or "").split("|"))
        events.append(chunk({}, "tool_calls" if calls else "stop"))
        if with_usage:
            events.append(
                {
                    "id": "chatcmpl-local",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }
            )
        encoded = [f"data: {json.dumps(item)}\n\n".encode() for item in events]
        return b"".join(encoded) + (b"data: [DONE]\n\n" if terminal else b"")
    final = response(protocol, text=(text or "").replace("|", ""), calls=calls)
    if not with_usage:
        final.pop("usage")
    initial = {**final, "status": "in_progress", "output": [], "usage": None}
    events.append({"type": "response.created", "response": initial})
    for index, item in enumerate(final["output"]):
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        if not calls:
            for part in (text or "").split("|"):
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": item["id"],
                        "output_index": index,
                        "content_index": 0,
                        "delta": part,
                        "logprobs": [],
                    }
                )
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    if terminal:
        events.append({"type": "response.completed", "response": final})
    return b"".join(
        (
            f"event: {item['type']}\n"
            + f"data: {json.dumps({**item, 'sequence_number': index})}\n\n"
        ).encode()
        for index, item in enumerate(events)
    )
