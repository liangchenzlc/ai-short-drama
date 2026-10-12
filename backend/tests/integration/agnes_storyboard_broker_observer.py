"""分镜专用传输观测；委托真实调用，不增加流等待或改写供应商正文。"""

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from short_drama.core.config import Settings
from short_drama.tasks import worker
from tests.integration.agnes_generation_broker_observer import (
    ObservedGateway as BrokerObservedGateway,
)
from tests.integration.agnes_generation_broker_observer import directory
from tests.integration.test_canvas_agnes_live import ObservedTransport, assert_no_secret

REQUEST_FILE = "supplier-storyboard-request.json"
RESPONSE_FILE = "supplier-storyboard-sse.bin"
REQUEST_FIELDS = {"messages", "model", "stream", "stream_options"}


def response_mime(headers: Mapping[str, str]) -> str:
    return next((value for key, value in headers.items() if key.lower() == "content-type"), "")


def sse_statistics(data: bytes, secret: str) -> tuple[int, bool, list[str]]:
    """仅统计可识别的 Chat SSE 数据事件；原字节保持，不代替正式解析器。"""
    assert secret.encode("utf-8") not in data, "A live credential appeared in non-secret state"
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        # 统计不能识别的编码不改变原响应；正式解析器仍处理原字节。
        return 0, False, []
    assert_no_secret(text, secret)
    delta_events, done, finish_reasons = 0, False, []
    for block in text.split("\n\n"):
        payload = "\n".join(
            line[5:].removeprefix(" ") for line in block.split("\n") if line.startswith("data:")
        )
        if not payload:
            continue
        if payload == "[DONE]":
            done = True
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            # 无法识别的正文仍逐字归档，由原解析器决定运行结果。
            continue
        assert_no_secret(event, secret)
        if not isinstance(event, dict) or not isinstance(event.get("choices"), list):
            continue
        has_text = False
        for choice in event["choices"]:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                has_text = has_text or isinstance(content, str) and bool(content)
            reason = choice.get("finish_reason")
            if isinstance(reason, str):
                finish_reasons.append(reason)
        delta_events += int(has_text)
    return delta_events, done, finish_reasons


class StoryboardTransport:
    """包裹原观测 transport；成功返回后独占归档已解码的实际响应字节。"""

    def __init__(
        self,
        delegate: ObservedTransport,
        report: dict[str, Any],
        output_directory: Path,
        checkpoint: Callable[[], None],
    ) -> None:
        self.delegate = delegate
        self.report = report
        self.directory = output_directory
        self.checkpoint = checkpoint

    def request(self, method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], bytes]:
        body = kwargs.get("body")
        assert isinstance(body, dict) and set(body) == REQUEST_FIELDS
        headers = kwargs.get("headers") or {}
        authorization = next(
            (value for key, value in headers.items() if key.lower() == "authorization"), ""
        )
        scheme, separator, secret = authorization.partition(" ")
        assert separator and scheme.lower() == "bearer" and secret
        assert_no_secret(body, secret)
        # 与 SafeTransport 的逻辑 JSON 编码选项一致；不含任何请求头，不是网络抓包。
        request_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        with (self.directory / REQUEST_FILE).open("xb") as handle:
            handle.write(request_data)
        wire: dict[str, Any] = {
            "scope": (
                "logical request JSON serialization and successful SafeTransport decoded response "
                "bytes; no HTTP headers, TLS or network capture; recognizable index-0 text SSE "
                "events are separate from transport callback chunks"
            ),
            "request_file": REQUEST_FILE,
            "request_sha256": hashlib.sha256(request_data).hexdigest(),
            "response_file": None,
            "response_sha256": None,
            "response_bytes": None,
            "response_mime": None,
            "callback_chunk_count": 0,
            "sse_delta_event_count": 0,
            "done": False,
            "finish_reasons": [],
        }
        self.report["wire_observation"] = wire
        self.checkpoint()
        original_headers = kwargs.get("on_headers")
        original_chunk = kwargs.get("on_chunk")

        def observe_headers(status: int, response_headers: dict[str, str]) -> None:
            mime = response_mime(response_headers)
            assert_no_secret(mime, secret)
            wire["response_mime"] = mime
            if original_headers is not None:
                original_headers(status, response_headers)

        def observe_chunk(chunk: bytes) -> None:
            wire["callback_chunk_count"] += 1
            if original_chunk is not None:
                original_chunk(chunk)

        status, response_headers, data = self.delegate.request(
            method,
            url,
            **{**kwargs, "on_headers": observe_headers, "on_chunk": observe_chunk},
        )
        mime = response_mime(response_headers)
        assert_no_secret(mime, secret)
        wire["response_mime"] = mime
        wire["response_bytes"] = len(data)
        if 200 <= status < 300:
            delta_events, done, finish_reasons = sse_statistics(data, secret)
            with (self.directory / RESPONSE_FILE).open("xb") as handle:
                handle.write(data)
            wire.update(
                response_file=RESPONSE_FILE,
                response_sha256=hashlib.sha256(data).hexdigest(),
                sse_delta_event_count=delta_events,
                done=done,
                finish_reasons=finish_reasons,
            )
        assert_no_secret(wire, secret)
        self.checkpoint()
        return status, response_headers, data

    def download_media(self, url: str, max_bytes: int, **kwargs: Any) -> tuple[bytes, str]:
        return self.delegate.download_media(url, max_bytes, **kwargs)


class ObservedGateway(BrokerObservedGateway):
    """继承原真实网关和已注册的正式 Celery hooks，仅增加分镜观测层。"""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.report["wire_observation"] = None
        self.transport = StoryboardTransport(
            self.transport, self.report, directory, self.checkpoint
        )
        self.checkpoint()


worker.GenerationGateway = ObservedGateway
