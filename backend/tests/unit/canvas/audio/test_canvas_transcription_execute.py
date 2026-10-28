"""本地 whisper HTTP 协议及 MIME 合同；HTTP transport 为明确的测试替身。"""

import asyncio
import io
import wave

import httpx
import pytest

import short_drama.service.canvas.tools.transcription.transcription_execute as module
from short_drama.core.config import Settings
from short_drama.service.canvas.tools.transcription.transcription_execute import (
    CanvasTranscriptionExecutor,
    is_transcribable_mime,
    transcription_extension,
)
from short_drama.service.creation.assembly.rendering.renderer import RenderCancelled


@pytest.mark.parametrize(
    ("mime", "extension"),
    [
        ("video/mp4", ".mp4"),
        ("audio/mp4; codec=aac", ".mp4"),
        ("video/webm", ".webm"),
        ("audio/webm", ".webm"),
        ("video/quicktime", ".mov"),
        ("audio/mpeg", ".mp3"),
        ("audio/mp3", ".mp3"),
        ("audio/wav", ".wav"),
        ("audio/x-wav", ".wav"),
        ("audio/wave", ".wav"),
        ("audio/flac", ".flac"),
        ("audio/aac", ".aac"),
        ("audio/ogg", ".ogg"),
        ("video/ogg", ".ogg"),
        ("audio/opus", ".bin"),
        ("AUDIO/WAV", ".bin"),
        ("", ".bin"),
    ],
)
def test_mime_prefix_and_extension_remain_case_sensitive(mime, extension):
    assert transcription_extension(mime) == extension
    assert is_transcribable_mime(mime) == mime.startswith(("audio/", "video/"))


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "audio16k.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(bytes(3200))
    return path


def executor(handler, progress=lambda *_: None, base=" http://127.0.0.1:8765/prefix/ "):
    return CanvasTranscriptionExecutor(
        Settings(_env_file=None, canvas_whisper_base_url=base),
        progress=progress,
        transport=httpx.MockTransport(handler),
    )


def test_multipart_uses_source_route_response_format_language_and_file(wav):
    requests = []

    async def handler(request):
        body = await request.aread()
        requests.append(request)
        assert str(request.url) == "http://127.0.0.1:8765/prefix/inference"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        assert b'name="file"; filename="audio16k.wav"' in body
        assert wav.read_bytes() in body
        assert b'name="response_format"\r\n\r\nverbose_json' in body
        assert b'name="language"\r\n\r\nzh' in body
        return httpx.Response(
            200,
            json={"segments": [{"start": 0, "end": 1.0005, "text": " 中文 "}], "language": "zh"},
        )

    result = executor(handler).transcribe(wav, "\u3000 zh \n")
    assert len(requests) == 1
    assert result.segments[0].end_ms == 1001 and result.segments[0].text == "中文"
    assert result.srt == "1\n00:00:00,000 --> 00:00:01,001\n中文\n\n"


def test_blank_language_is_omitted_and_empty_speech_is_reported(wav):
    async def handler(request):
        assert b'name="language"' not in await request.aread()
        return httpx.Response(200, json={"text": "not structured", "segments": []})

    with pytest.raises(RuntimeError, match="本地转写服务未识别出语音内容"):
        executor(handler).transcribe(wav, "\t\n\u3000")


def test_error_response_reads_only_source_2048_byte_excerpt(wav):
    with pytest.raises(RuntimeError) as failure:
        executor(
            lambda _: httpx.Response(503, content=b" upstream unavailable " + b"x" * 5000)
        ).transcribe(wav)
    assert str(failure.value).startswith("本地转写服务返回 503: upstream unavailable ")
    assert len(str(failure.value).split(": ", 1)[1]) == 2047


@pytest.mark.parametrize("payload", [b"invalid json", b'{"segments":[{"end":1e309}]}'])
def test_invalid_success_response_is_not_accepted(wav, payload):
    with pytest.raises(RuntimeError, match="转写结果解析失败"):
        executor(lambda _: httpx.Response(200, content=payload)).transcribe(wav)


def test_eight_mib_source_limit_prevents_decoding_an_unbounded_payload(wav):
    raw = b'{"language":"' + b"x" * (8 << 20) + b'","segments":[{"text":"body"}]}'
    with pytest.raises(RuntimeError, match="转写结果解析失败"):
        executor(lambda _: httpx.Response(200, content=raw)).transcribe(wav)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadError("connection closed"),
        httpx.ReadTimeout("read timeout"),
    ],
)
def test_transport_errors_include_operation_and_original_cause(wav, error):
    def handler(_):
        raise error

    with pytest.raises(RuntimeError, match=r"无法连接本地转写服务\(whisper.cpp\)") as failure:
        executor(handler).transcribe(wav)
    assert failure.value.__cause__ is error


def test_progress_cancellation_aborts_inflight_http_without_retry(wav):
    begun = []
    cancelled = []

    async def handler(_):
        begun.append(True)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    def progress(stage, percent):
        assert (stage, percent) == ("正在转写…", 40)
        raise RenderCancelled()

    with pytest.raises(RenderCancelled):
        executor(handler, progress).transcribe(wav)
    assert begun == [True] and cancelled == [True]


def test_unconfigured_service_and_missing_file_fail_with_context(wav):
    with pytest.raises(RuntimeError, match="CANVAS_WHISPER_BASE_URL"):
        executor(lambda _: None, base="").transcribe(wav)
    with pytest.raises(RuntimeError, match="读取待转写音频失败"):
        executor(lambda _: None).transcribe(wav.parent / "missing.wav")


def test_invalid_media_is_rejected_without_reading_or_connecting():
    class Unreadable(io.BytesIO):
        def read(self, *args):
            raise AssertionError("invalid MIME must not read media")

    with pytest.raises(RuntimeError, match="仅支持音视频文件转写"):
        executor(lambda _: pytest.fail("must not call service")).run(Unreadable(), "image/png")


def test_twenty_minute_deadline_is_total_and_cancels_request(wav, monkeypatch):
    cancelled = []
    monkeypatch.setattr(module, "WHISPER_TIMEOUT_SECONDS", 0.05)

    async def handler(_):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with pytest.raises(RuntimeError, match="转写请求超时"):
        executor(handler).transcribe(wav)
    assert cancelled == [True]
