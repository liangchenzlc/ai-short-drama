"""Local provider wire responses for tests that retain the real protected SDK stack."""

import json


class JSONChatTransport:
    def __init__(self, output):
        self.output, self.requests = output, []

    def request(self, method, url, *, body, on_send, **kwargs):
        on_send()
        self.requests.append(body)
        content = json.dumps(
            {
                "id": "controlled-memory",
                "object": "chat.completion",
                "created": 1,
                "model": "controlled",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(self.output, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
            }
        ).encode()
        return 200, {"Content-Type": "application/json"}, content
