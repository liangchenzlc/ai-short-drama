"""有界 FFmpeg 转码；临时文件和进程沿用媒体执行器的租约与清理。"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from short_drama.service.creation.assembly.rendering.renderer import VideoRenderer, executable


@contextmanager
def compatible_video(reader: BinaryIO, settings, heartbeat) -> Iterator[tuple[Path, dict]]:
    root = Path(settings.render_scratch_root)
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="canvas-playback-", dir=root) as temporary:
        directory = Path(temporary)
        source, target = directory / "source.bin", directory / "playback.mp4"
        size = 0
        with source.open("wb") as output:
            while block := reader.read(1024 * 1024):
                size += len(block)
                if size > min(settings.render_max_source_bytes, settings.render_max_scratch_bytes):
                    raise RuntimeError("视频超过兼容预览处理上限")
                output.write(block)
                heartbeat("读取原视频", 20)
        renderer = VideoRenderer(settings, heartbeat)
        renderer.run(
            [
                executable(settings.render_ffmpeg_path),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                "-threads",
                "2",
                str(target),
            ],
            directory,
            stage="准备兼容预览",
            progress=55,
        )
        if not target.is_file() or not target.stat().st_size:
            raise RuntimeError("兼容预览转码未输出有效视频")
        yield target, renderer.probe(target)
