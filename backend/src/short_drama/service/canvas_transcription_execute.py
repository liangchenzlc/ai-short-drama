"""whisper.cpp 本地协议与真实 WAV 预处理；退出上下文即清理临时媒体。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

import httpx

from short_drama.core.config import Settings
from short_drama.schemas.canvas_transcription import CanvasTranscriptionResult

from .canvas_timeline_compile import go_trim
from .canvas_timeline_renderer import Progress
from .canvas_transcription import build_transcription_srt, decode_whisper_verbose_json
from .video_render import VideoRenderer, executable

WHISPER_TIMEOUT_SECONDS = 20 * 60
WHISPER_RESPONSE_BYTES = 8 << 20
WHISPER_ERROR_BYTES = 2048


def is_transcribable_mime(mime: str) -> bool:
    return mime.startswith(("audio/", "video/"))


def transcription_extension(mime: str) -> str:
    for prefixes, extension in (
        (("video/mp4", "audio/mp4"), ".mp4"),
        (("video/webm", "audio/webm"), ".webm"),
        (("video/quicktime",), ".mov"),
        (("audio/mpeg", "audio/mp3"), ".mp3"),
        (("audio/wav", "audio/x-wav", "audio/wave"), ".wav"),
        (("audio/flac",), ".flac"),
        (("audio/aac",), ".aac"),
        (("audio/ogg", "video/ogg"), ".ogg"),
    ):
        if mime.startswith(prefixes):
            return extension
    return ".bin"


@contextmanager
def prepare_transcription_wav(
    reader: BinaryIO, mime: str, settings: Settings, progress: Progress = lambda *_: None
) -> Iterator[Path]:
    try:
        ffmpeg = executable(settings.render_ffmpeg_path)
    except RuntimeError as error:
        raise RuntimeError("音频预处理依赖未安装（需要 ffmpeg）") from error
    try:
        root = Path(settings.render_scratch_root)
        root.mkdir(parents=True, exist_ok=True)
        temporary = TemporaryDirectory(prefix="beeftv-whisper-", dir=root)
    except OSError as error:
        raise RuntimeError("创建转写临时目录失败") from error
    with temporary as name:
        directory = Path(name)
        input_path = directory / ("input" + transcription_extension(mime))
        size = 0
        try:
            with input_path.open("wb") as output:
                while block := reader.read(1024 * 1024):
                    size += len(block)
                    if size > min(
                        settings.render_max_source_bytes, settings.render_max_scratch_bytes
                    ):
                        raise RuntimeError("待转写媒体超过本机临时空间上限")
                    output.write(block)
                    progress("等待转写服务", 15)
        except OSError as error:
            raise RuntimeError("读取或写入待转写媒体失败") from error
        wav_path = directory / "audio16k.wav"
        try:
            VideoRenderer(settings, progress).run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-y",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(input_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
                directory,
                stage="等待转写服务",
                progress=15,
            )
            if not wav_path.is_file() or wav_path.stat().st_size == 0:
                raise RuntimeError("FFmpeg 未输出可用 WAV")
            if (
                sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
                > settings.render_max_scratch_bytes
            ):
                raise RuntimeError("转写临时空间达到上限")
        except (RuntimeError, OSError) as error:
            detail = ""
            log = directory / "process.stderr"
            try:
                if log.is_file():
                    with log.open("rb") as stream:
                        stream.seek(max(0, log.stat().st_size - 400))
                        detail = go_trim(stream.read(400).decode(errors="replace"))
            except OSError as read_error:
                raise RuntimeError("音频预处理失败，且无法读取 FFmpeg 诊断") from read_error
            raise RuntimeError(
                f"音频预处理失败（ffmpeg）: {error}" + (f"；{detail}" if detail else "")
            ) from error
        yield wav_path


class CanvasTranscriptionExecutor:
    def __init__(
        self,
        settings: Settings,
        progress: Progress = lambda *_: None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings, self.progress, self.transport = settings, progress, transport

    def run(self, reader: BinaryIO, mime: str, language: str = "") -> CanvasTranscriptionResult:
        if not is_transcribable_mime(mime):
            raise RuntimeError("仅支持音视频文件转写")
        self.progress("等待转写服务", 15)
        with prepare_transcription_wav(reader, mime, self.settings, self.progress) as wav_path:
            self.progress("正在转写…", 40)
            result = self.transcribe(wav_path, language)
            self.progress("整理字幕…", 80)
            return result

    def transcribe(self, wav_path: Path, language: str = "") -> CanvasTranscriptionResult:
        base_url = go_trim(self.settings.canvas_whisper_base_url).rstrip("/")
        if not base_url:
            raise RuntimeError("本地转写服务未配置：请设置 CANVAS_WHISPER_BASE_URL")
        try:
            audio = wav_path.open("rb")
        except OSError as error:
            raise RuntimeError("读取待转写音频失败") from error
        with audio:
            return asyncio.run(self._observe_request(base_url, audio, wav_path.name, language))

    async def _observe_request(
        self, base_url: str, audio: BinaryIO, filename: str, language: str
    ) -> CanvasTranscriptionResult:
        request = asyncio.create_task(self._request(base_url, audio, filename, language))
        try:
            async with asyncio.timeout(WHISPER_TIMEOUT_SECONDS):
                while True:
                    done, _ = await asyncio.wait({request}, timeout=0.25)
                    if done:
                        return await request
                    self.progress("正在转写…", 40)
        except TimeoutError as error:
            raise RuntimeError("无法连接本地转写服务(whisper.cpp): 转写请求超时") from error
        finally:
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)

    async def _request(
        self, base_url: str, audio: BinaryIO, filename: str, language: str
    ) -> CanvasTranscriptionResult:
        fields = {"response_format": "verbose_json"}
        if go_trim(language):
            fields["language"] = go_trim(language)
        try:
            async with httpx.AsyncClient(
                timeout=WHISPER_TIMEOUT_SECONDS,
                follow_redirects=True,
                trust_env=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST",
                    base_url + "/inference",
                    data=fields,
                    files={"file": (filename, audio, "application/octet-stream")},
                ) as response:
                    limit = (
                        WHISPER_RESPONSE_BYTES
                        if response.status_code == 200
                        else WHISPER_ERROR_BYTES
                    )
                    payload = bytearray()
                    async for block in response.aiter_bytes(64 * 1024):
                        payload.extend(block[: limit - len(payload)])
                        if len(payload) >= limit:
                            break
                    if response.status_code != 200:
                        detail = go_trim(payload.decode(errors="replace"))
                        raise RuntimeError(f"本地转写服务返回 {response.status_code}: {detail}")
        except httpx.InvalidURL as error:
            raise RuntimeError("本地转写服务地址无效，请检查 CANVAS_WHISPER_BASE_URL") from error
        except httpx.HTTPError as error:
            raise RuntimeError(f"无法连接本地转写服务(whisper.cpp): {error}") from error
        segments, language_out = decode_whisper_verbose_json(bytes(payload))
        if not segments:
            raise RuntimeError("本地转写服务未识别出语音内容")
        return CanvasTranscriptionResult(
            segments=segments, language=language_out, srt=build_transcription_srt(segments)
        )
