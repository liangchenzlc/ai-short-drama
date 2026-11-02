"""Controlled OpenAI Responses SSE wire, split independently of UTF-8 boundaries."""

import json

from tests.support.assistant_http import response


def reasoning_wire(thoughts=("雨🙂", "声"), texts=("正文雨🙂", "声")):
    final = response("responses", text="".join(texts))
    reasoning = {
        "type": "reasoning",
        "id": "rs_trace",
        "summary": [{"type": "summary_text", "text": "".join(thoughts)}],
        "encrypted_content": "private-cipher",
    }
    final["output"].insert(0, reasoning)
    created = {**final, "status": "in_progress", "output": [], "usage": None}
    events = [
        {"type": "response.created", "response": created},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**reasoning, "summary": []},
        },
        {
            "type": "response.reasoning_summary_part.added",
            "output_index": 0,
            "item_id": "rs_trace",
            "summary_index": 0,
            "part": {"type": "summary_text", "text": ""},
        },
    ]
    events.extend(
        {
            "type": "response.reasoning_summary_text.delta",
            "item_id": "rs_trace",
            "output_index": 0,
            "summary_index": 0,
            "delta": text,
        }
        for text in thoughts
    )
    events.append({"type": "response.output_item.done", "output_index": 0, "item": reasoning})
    message = final["output"][1]
    events.append(
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {**message, "content": []},
        }
    )
    events.extend(
        {
            "type": "response.output_text.delta",
            "item_id": message["id"],
            "output_index": 1,
            "content_index": 0,
            "delta": text,
            "logprobs": [],
        }
        for text in texts
    )
    events.extend(
        [
            {"type": "response.output_item.done", "output_index": 1, "item": message},
            {"type": "response.completed", "response": final},
        ]
    )
    return [
        b"event: "
        + item["type"].encode()
        + b"\ndata: "
        + json.dumps(item, ensure_ascii=False).encode()
        + b"\n\n"
        for item in events
    ]


class TraceWireTransport:
    def __init__(self, *, thoughts=("雨🙂", "声"), texts=("正文雨🙂", "声"), gate=None):
        self.parts, self.gate, self.requests = reasoning_wire(thoughts, texts), gate, []

    def request(self, _method, _url, *, body, on_send, on_headers, on_chunk, **_kwargs):
        on_send()
        self.requests.append(body)
        headers = {"Content-Type": "text/event-stream"}
        on_headers(200, headers)
        for index, data in enumerate(self.parts):
            for start in range(0, len(data), 11):
                on_chunk(data[start : start + 11])
            if index == 3 and self.gate:
                assert self.gate.wait(15), "test did not release partial reasoning stream"
        return 200, headers, b"".join(self.parts)
