"""Controlled provider wire for the real AgentRuntime / SDK / MySQL graph."""

import json


class WorkerLost(BaseException):
    """模拟进程消失：绕过普通异常处理，保留最后一次已提交的数据库状态。"""


class RuntimeTransport:
    def __init__(self, replies, *, before_reply=None, selections=None):
        self.replies = iter(replies)
        self.selections = iter(selections) if selections is not None else None
        self.before_reply = before_reply
        self.requests = []
        self.main_requests = []

    @property
    def selection_requests(self):
        return [
            body
            for body in self.requests
            if any(
                "tools" in item.get("function", {}).get("parameters", {}).get("properties", {})
                for item in body.get("tools", [])
            )
        ]

    def request(self, method, url, *, body, on_send, **kwargs):
        on_send()
        self.requests.append(body)
        functions = [item.get("function", {}) for item in body.get("tools", [])]
        selection = next(
            (
                item
                for item in functions
                if "tools" in item.get("parameters", {}).get("properties", {})
            ),
            None,
        )
        if selection:
            parameter = selection["parameters"]["properties"]["tools"]["items"]
            names = parameter.get("enum", [parameter["const"]] if "const" in parameter else [])
            chosen = next(self.selections) if self.selections is not None else names[:16]
            reply = {"calls": [("selector", selection["name"], {"tools": chosen})]}
        else:
            self.main_requests.append(body)
            if self.before_reply:
                self.before_reply(body, len(self.main_requests))
            reply = next(self.replies)
            if callable(reply):
                reply = reply(body)
        message = {"role": "assistant", "content": reply.get("text", "")}
        if reply.get("calls"):
            message["tool_calls"] = [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }
                for call_id, name, args in reply["calls"]
            ]
        finish_reason = reply.get("finish_reason", "tool_calls" if reply.get("calls") else "stop")
        content = json.dumps(
            {
                "id": f"controlled-{len(self.requests)}",
                "object": "chat.completion",
                "created": 1,
                "model": "controlled",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
            ensure_ascii=False,
        ).encode()
        headers = {"Content-Type": "application/json"}
        if body.get("stream"):
            delta = {"role": "assistant", "content": message["content"]}
            if message.get("tool_calls"):
                delta["tool_calls"] = [
                    dict(item, index=index) for index, item in enumerate(message["tool_calls"])
                ]
            chunk = {
                "id": f"controlled-{len(self.requests)}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "controlled",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            end = {
                **chunk,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": finish_reason,
                    }
                ],
            }
            usage = {
                **chunk,
                "choices": [],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            }
            content = (
                b"".join(
                    b"data: " + json.dumps(item, ensure_ascii=False).encode() + b"\n\n"
                    for item in (chunk, end, usage)
                )
                + b"data: [DONE]\n\n"
            )
            headers = {"Content-Type": "text/event-stream"}
            kwargs["on_headers"](200, headers)
            kwargs["on_chunk"](content)
        return 200, headers, content


def calls(*items):
    return {"calls": items}


def tool_result(body, call_id):
    return json.loads(
        next(
            item["content"]
            for item in body["messages"]
            if item["role"] == "tool" and item["tool_call_id"] == call_id
        )
    )
