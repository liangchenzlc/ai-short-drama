"""已授权媒体字节的本机时间线渲染；产物必须在上下文内归档，退出即清理。"""

import json
import math
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import BinaryIO

from short_drama.core.config import Settings
from short_drama.schemas.canvas.timeline.timeline import CanvasTimelinePlanRead
from short_drama.service.canvas.timeline.timeline_native import (
    SUBTITLE_FILE_NAME,
    SourceFacts,
    apply_source_facts,
    build_ffmpeg_args,
    output_duration_within_plan,
    source_ids,
    subtitle_font_failure,
)
from short_drama.service.creation.assembly.rendering.renderer import executable

SourceOpener = Callable[[str], AbstractContextManager[BinaryIO]]
Progress = Callable[[str, int], None]


@dataclass(frozen=True)
class RenderedTimeline:
    path: Path
    size: int
    width: int
    height: int
    duration_ms: int
    subtitle_srt: str


@dataclass(frozen=True)
class ProbedMedia:
    facts: SourceFacts
    width: int
    height: int


class BoundedEncoderLog:
    """保留早期/跨块字体失败判断，同时只保留最后64KiB诊断。"""

    def __init__(self) -> None:
        self.tail = b""
        self.scan_tail = b""
        self.font_failure = False

    def write(self, data: bytes) -> None:
        scan = self.scan_tail + data
        self.font_failure = self.font_failure or subtitle_font_failure(
            scan.decode(errors="replace")
        )
        self.scan_tail = scan[-256:]
        self.tail = (self.tail + data)[-(64 << 10) :]

    def detail(self) -> str:
        return self.tail[-400:].decode(errors="replace").strip()


class CanvasTimelineRenderer:
    def __init__(self, settings: Settings, progress: Progress = lambda *_: None) -> None:
        self.settings = settings
        self.progress = progress

    def probe(self, path: Path) -> ProbedMedia:
        try:
            result = subprocess.run(
                [
                    executable(self.settings.render_ffprobe_path),
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode or len(result.stdout) > 1024 * 1024:
                raise ValueError("unreadable media")
            parsed = json.loads(result.stdout)
            if not isinstance(parsed, dict):
                raise ValueError("invalid media probe response")
            streams = parsed["streams"]
            container = parsed.get("format", {})
            if (
                not isinstance(streams, list)
                or any(not isinstance(item, dict) for item in streams)
                or not isinstance(container, dict)
            ):
                raise ValueError("invalid media probe structure")
            video = next((item for item in streams if item.get("codec_type") == "video"), None)
            has_audio = any(item.get("codec_type") == "audio" for item in streams)
            raw_duration = container.get("duration", "")
            try:
                seconds = float(raw_duration)
                duration = int(seconds * 1000) if math.isfinite(seconds) and seconds > 0 else 0
            except (ValueError, TypeError):
                duration = 0
            return ProbedMedia(
                SourceFacts(has_audio, video is not None, duration),
                int((video or {}).get("width", 0)),
                int((video or {}).get("height", 0)),
            )
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
            subprocess.TimeoutExpired,
            RuntimeError,
        ) as error:
            raise RuntimeError("媒体音轨探测失败，无法解析时间线素材") from error

    def _run(self, arguments: list[str], directory: Path, burn_subtitles: bool) -> None:
        log = BoundedEncoderLog()
        try:
            process = subprocess.Popen(
                arguments,
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise RuntimeError("无法启动时间线编码器，请检查 FFmpeg 配置") from error
        with process:
            assert process.stdout is not None
            reader_errors: list[Exception] = []

            def collect() -> None:
                try:
                    while block := process.stdout.read(4096):
                        log.write(block)
                except Exception as error:
                    reader_errors.append(error)

            reader = Thread(target=collect, name="canvas-timeline-encoder-log", daemon=True)
            reader.start()
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if reader_errors:
                        raise RuntimeError("时间线编码日志读取失败") from reader_errors[0]
                    self.progress("正在渲染…", 30)
                    if time.monotonic() - started > self.settings.render_timeout_seconds:
                        raise RuntimeError("时间线渲染超时，请缩短成片后重试")
                    self._check_scratch(directory)
                    time.sleep(0.1)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                reader.join(5)
                if reader.is_alive():
                    raise RuntimeError("时间线编码日志读取未结束")
            if reader_errors:
                raise RuntimeError("时间线编码日志读取失败") from reader_errors[0]
            self._check_scratch(directory)
            if process.returncode or (burn_subtitles and log.font_failure):
                raise RuntimeError("ffmpeg 时间线渲染失败：" + (log.detail() or "编码器未返回诊断"))

    def _check_scratch(self, directory: Path) -> None:
        try:
            used = sum(item.stat().st_size for item in directory.iterdir() if item.is_file())
        except OSError as error:
            raise RuntimeError("无法检查时间线渲染临时空间") from error
        if used > self.settings.render_max_scratch_bytes:
            raise RuntimeError("时间线渲染临时空间达到上限")

    @contextmanager
    def render(
        self, plan: CanvasTimelinePlanRead, open_source: SourceOpener
    ) -> Iterator[RenderedTimeline]:
        if not source_ids(plan):
            raise RuntimeError("时间线没有可渲染的媒体片段")
        ffmpeg = executable(self.settings.render_ffmpeg_path)
        self.progress("准备媒体…", 10)
        root = Path(self.settings.render_scratch_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="canvas-timeline-", dir=root) as temporary:
            directory = Path(temporary).resolve()
            if directory == root or not directory.is_relative_to(root):
                raise RuntimeError("时间线渲染临时目录无效")
            files: dict[str, str] = {}
            facts: dict[str, SourceFacts] = {}
            materialized = 0
            for identifier in source_ids(plan):
                self.progress("准备媒体…", 10)
                path = directory / f"src-{len(files)}.mp4"
                try:
                    with open_source(identifier) as source, path.open("wb") as target:
                        source_size = 0
                        while block := source.read(1024 * 1024):
                            source_size += len(block)
                            materialized += len(block)
                            if (
                                source_size > self.settings.render_max_source_bytes
                                or materialized > self.settings.render_max_scratch_bytes
                            ):
                                raise RuntimeError("时间线素材超过渲染空间上限")
                            self.progress("准备媒体…", 10)
                            target.write(block)
                except (OSError, ValueError) as error:
                    raise RuntimeError("无法读取时间线引用的媒体，可能已被删除") from error
                files[identifier] = path.name
                facts[identifier] = self.probe(path).facts
            compiled = plan.model_copy(deep=True)
            apply_source_facts(compiled, facts)
            if compiled.subtitle_srt:
                (directory / SUBTITLE_FILE_NAME).write_text(compiled.subtitle_srt, encoding="utf-8")
            target = directory / "render-output.mp4"
            args = build_ffmpeg_args(compiled, files, target.name)
            restricted: list[str] = []
            for item in args:
                if item == "-i":
                    restricted += ["-protocol_whitelist", "file,pipe"]
                restricted.append(item)
            self._run([ffmpeg, *restricted], directory, bool(compiled.subtitle_srt))
            self.progress("检查成片…", 90)
            if not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError("时间线渲染产物为空")
            output = self.probe(target)
            if not output.facts.has_video or not output.facts.has_audio:
                raise RuntimeError("时间线渲染产物缺少音视频轨")
            if not output_duration_within_plan(
                compiled.duration_ms, output.facts.duration_ms, compiled.output.fps
            ):
                raise RuntimeError("时间线渲染产物时长无效")
            if (output.width, output.height) != (compiled.output.width, compiled.output.height):
                raise RuntimeError("时间线渲染产物尺寸无效")
            yield RenderedTimeline(
                target,
                target.stat().st_size,
                output.width,
                output.height,
                output.facts.duration_ms,
                compiled.subtitle_srt,
            )
