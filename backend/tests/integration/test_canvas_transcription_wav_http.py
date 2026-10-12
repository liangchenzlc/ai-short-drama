"""真实 FFmpeg/WAV/本机 HTTP 专项；服务返回受控结果，不是 ASR 模型验收。"""

import hashlib
import io
import json
import os
import subprocess
import wave
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

from short_drama.core.config import Settings
from short_drama.service.canvas_transcription_execute import (
    CanvasTranscriptionExecutor,
    prepare_transcription_wav,
)
from short_drama.service.video_render import RenderCancelled, executable

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_TRANSCRIPTION_FFMPEG") != "1",
    reason="RUN_CANVAS_TRANSCRIPTION_FFMPEG=1 is required for real WAV/HTTP checks",
)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("transcription-media")
    files = {}
    for name, video, audio in (
        ("audio", False, True),
        ("video", True, True),
        ("silent", True, False),
    ):
        path = root / (name + (".mp4" if video else ".wav"))
        args = [executable("ffmpeg"), "-v", "error", "-nostdin", "-y"]
        if video:
            args += ["-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=0.4"]
        if audio:
            args += [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=0.4",
            ]
        if video:
            args += ["-c:v", "libx264"]
        if audio:
            args += ["-ac", "2", "-c:a", "aac" if video else "pcm_s16le"]
        subprocess.run([*args, str(path)], capture_output=True, check=True, timeout=30)
        files[name] = path
    return files


@contextmanager
def whisper_server(*, status=200, payload=None, wait_for_release=False):
    received = []
    release = Event()
    if payload is None:
        payload = {
            "segments": [{"start": 0.0005, "end": 0.35, "text": " 中文语音 "}],
            "language": "zh",
        }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            multipart = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body
            )
            fields = {
                part.get_param("name", header="content-disposition"): part
                for part in multipart.iter_parts()
            }
            received.append({"path": self.path, "fields": fields, "body": body})
            if wait_for_release:
                release.wait(5)
            content = (
                json.dumps(payload, ensure_ascii=False).encode()
                if isinstance(payload, dict)
                else payload
            )
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                received[-1]["client_closed"] = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def settings(tmp_path, **values):
    return Settings(_env_file=None, render_scratch_root=str(tmp_path / "scratch"), **values)


def assert_clean(tmp_path):
    assert list((tmp_path / "scratch").glob("beeftv-whisper-*")) == []


@pytest.mark.parametrize(("kind", "mime"), [("audio", "audio/wav"), ("video", "video/mp4")])
def test_real_preprocessing_and_multipart_preserve_source_and_return_subtitles(
    tmp_path, media, kind, mime
):
    path = media[kind]
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    progress = []
    with whisper_server() as (url, received):
        with path.open("rb") as reader:
            result = CanvasTranscriptionExecutor(
                settings(tmp_path, canvas_whisper_base_url=url),
                lambda *values: progress.append(values),
            ).run(reader, mime, " zh ")
        assert len(received) == 1 and received[0]["path"] == "/inference"
        fields = received[0]["fields"]
        assert fields["file"].get_filename() == "audio16k.wav"
        assert fields["response_format"].get_payload(decode=True) == b"verbose_json"
        assert fields["language"].get_payload(decode=True) == b"zh"
        with wave.open(io.BytesIO(fields["file"].get_payload(decode=True)), "rb") as wav:
            assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 16000, 2)
            assert 0.35 <= wav.getnframes() / wav.getframerate() <= 0.5
            assert any(wav.readframes(wav.getnframes()))
        assert result.model_dump(by_alias=True) == {
            "segments": [{"startMs": 1, "endMs": 350, "text": "中文语音"}],
            "language": "zh",
            "srt": "1\n00:00:00,001 --> 00:00:00,350\n中文语音\n\n",
        }
    assert progress[0] == ("等待转写服务", 15) and progress[-1] == ("整理字幕…", 80)
    assert ("正在转写…", 40) in progress
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert_clean(tmp_path)


@pytest.mark.parametrize(
    ("status", "payload", "message"),
    [
        (503, b"service unavailable", "返回 503"),
        (200, {"segments": []}, "未识别出语音"),
        (200, b"invalid response", "转写结果解析失败"),
    ],
)
def test_real_http_failure_and_empty_results_clean_preprocessed_files(
    tmp_path, media, status, payload, message
):
    with whisper_server(status=status, payload=payload) as (url, received):
        with media["audio"].open("rb") as reader, pytest.raises(RuntimeError, match=message):
            CanvasTranscriptionExecutor(settings(tmp_path, canvas_whisper_base_url=url)).run(
                reader, "audio/wav"
            )
        assert len(received) == 1
    assert_clean(tmp_path)


@pytest.mark.parametrize(
    "input_kind", ["silent", "malformed", "reader_failure", "size_limit", "missing_ffmpeg"]
)
def test_preprocessing_failure_has_context_and_never_calls_http(tmp_path, media, input_kind):
    class BrokenReader(io.BytesIO):
        def read(self, *args):
            raise OSError("test media read failure")

    values = {}
    if input_kind == "missing_ffmpeg":
        values["render_ffmpeg_path"] = "missing-transcription-ffmpeg-test"
    if input_kind == "size_limit":
        values["render_max_source_bytes"] = 1024
    data = media["silent" if input_kind == "silent" else "audio"].read_bytes()
    reader = (
        BrokenReader()
        if input_kind == "reader_failure"
        else io.BytesIO(b"bad media" if input_kind == "malformed" else data)
    )
    message = {
        "reader_failure": "读取或写入待转写媒体失败",
        "size_limit": "超过本机临时空间上限",
        "missing_ffmpeg": "依赖未安装",
    }.get(input_kind, "音频预处理失败")
    with whisper_server() as (url, received):
        with reader, pytest.raises(RuntimeError, match=message):
            CanvasTranscriptionExecutor(
                settings(tmp_path, canvas_whisper_base_url=url, **values)
            ).run(reader, "video/mp4")
        assert received == []
    assert_clean(tmp_path)


def test_wav_context_cleans_after_caller_failure(tmp_path, media):
    with media["audio"].open("rb") as reader, pytest.raises(RenderCancelled):
        with prepare_transcription_wav(reader, "audio/wav", settings(tmp_path)) as path:
            assert path.is_file()
            raise RenderCancelled()
    assert_clean(tmp_path)


def test_cancellation_during_waiting_service_stops_request_and_cleans_wav(tmp_path, media):
    with whisper_server(wait_for_release=True) as (url, received):

        def progress(stage, _):
            if stage == "正在转写…" and received:
                raise RenderCancelled()

        with media["audio"].open("rb") as reader, pytest.raises(RenderCancelled):
            CanvasTranscriptionExecutor(
                settings(tmp_path, canvas_whisper_base_url=url), progress
            ).run(reader, "audio/wav")
        assert len(received) == 1
    assert_clean(tmp_path)
