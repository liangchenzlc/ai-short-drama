"""Bounded local FFmpeg operations. Inputs are downloaded managed media only."""

import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path


class RenderCancelled(Exception):
    pass


def executable(configured):
    found = shutil.which(configured)
    if found:
        return found
    # Portable development tools are intentionally untracked.
    name = Path(configured).name
    if name in ("ffmpeg", "ffprobe"):
        for candidate in Path(".tools/ffmpeg").glob(f"*/bin/{name}.exe"):
            return str(candidate.resolve())
    raise RuntimeError("未找到 FFmpeg 或 ffprobe，请配置合成服务后重试")


def checksum(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class VideoRenderer:
    def __init__(self, settings, heartbeat=lambda *_: None):
        self.settings, self.heartbeat = settings, heartbeat

    def run(self, arguments, directory, *, stage, progress):
        self.heartbeat(stage, progress)
        started = time.monotonic()
        with (directory / "process.stderr").open("wb") as stderr:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                while process.poll() is None:
                    self.heartbeat(stage, progress)
                    if time.monotonic() - started > self.settings.render_timeout_seconds:
                        raise RuntimeError("合成超时，请缩短成片后重试")
                    if (
                        sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
                        > self.settings.render_max_scratch_bytes
                    ):
                        raise RuntimeError("合成临时空间达到上限，请缩短成片后重试")
                    time.sleep(0.5)
                if process.returncode:
                    raise RuntimeError("视频无法解码或合成失败，请检查来源视频后重试")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def probe(self, path):
        try:
            probe_binary = executable(self.settings.render_ffprobe_path)
        except RuntimeError:
            return self._probe_with_ffmpeg(path)
        result = subprocess.run(
            [
                probe_binary,
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
            raise RuntimeError("视频检测失败，请重新生成或替换视频")
        data = json.loads(result.stdout)
        video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
        duration = float(
            (video or {}).get("duration") or data.get("format", {}).get("duration") or 0
        )
        if (
            not video
            or not math.isfinite(duration)
            or duration <= 0
            or not video.get("width")
            or not video.get("height")
        ):
            raise RuntimeError("视频缺少有效画面或实际时长")
        return {
            "probe_version": 1,
            "duration_ms": round(duration * 1000),
            "width": video["width"],
            "height": video["height"],
            "has_audio": any(s.get("codec_type") == "audio" for s in data["streams"]),
            "fps": video.get("avg_frame_rate", "0/1"),
        }

    def _probe_with_ffmpeg(self, path):
        """Portable fallback: demux metadata and decode a frame using installed FFmpeg."""
        result = subprocess.run(
            [
                executable(self.settings.render_ffmpeg_path),
                "-hide_banner",
                "-nostdin",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        report = result.stderr.decode("utf-8", errors="replace")
        duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", report)
        stream = re.search(r"Stream[^\n]+Video:[^\n]+", report)
        shape = re.search(r"\b(\d{2,5})x(\d{2,5})\b", stream[0]) if stream else None
        fps = re.search(r"([\d.]+) fps", stream[0]) if stream else None
        if result.returncode or not duration or not shape:
            raise RuntimeError("视频检测失败，请替换来源视频")
        milliseconds = round(
            (int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) * 1000
        )
        if milliseconds <= 0:
            raise RuntimeError("视频缺少有效时长")
        return {
            "probe_version": 1,
            "probe_engine": "ffmpeg",
            "duration_ms": milliseconds,
            "width": int(shape[1]),
            "height": int(shape[2]),
            "has_audio": bool(re.search(r"Stream[^\n]+Audio:", report)),
            "fps": fps[1] if fps else "0",
        }

    def preview_assets(self, source, info, directory):
        """Browser-compatible proxy and a bounded filmstrip, derived once per source."""
        ffmpeg = executable(self.settings.render_ffmpeg_path)
        proxy = directory / "proxy.mp4"
        thumbnail = directory / "thumbnail.jpg"
        frames = max(1, (info["duration_ms"] * 30 + 500) // 1000)
        self.run(
            [
                ffmpeg,
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
                f"scale=-2:360,fps=30,tpad=stop_mode=clone:stop_duration=0.04,trim=end_frame={frames},setpts=PTS-STARTPTS",
                "-af",
                "aresample=48000:async=1:first_pts=0,apad",
                "-t",
                str(frames / 30),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "26",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "30",
                "-bf",
                "0",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                "-threads",
                "2",
                str(proxy),
            ],
            directory,
            stage="preparing",
            progress=40,
        )
        self.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(proxy),
                "-vf",
                "thumbnail,scale=320:-2",
                "-frames:v",
                "1",
                str(thumbnail),
            ],
            directory,
            stage="preparing",
            progress=60,
        )
        filmstrip = directory / "filmstrip.jpg"
        count = min(24, max(1, (frames + 29) // 30))
        self.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(proxy),
                "-vf",
                f"fps={count * 30}/{frames}:start_time=0:round=up,"
                "scale=160:90:force_original_aspect_ratio=decrease,"
                "pad=160:90:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"tile={count}x1:nb_frames={count}",
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(filmstrip),
            ],
            directory,
            stage="preparing",
            progress=70,
        )
        info["filmstrip_count"] = count
        info["filmstrip_interval_ms"] = frames * 1000 / 30 / count
        return proxy, thumbnail, filmstrip

    def render(self, snapshot, sources, directory):
        ffmpeg = executable(self.settings.render_ffmpeg_path)
        precise = snapshot.get("version", 1) >= 2
        height = {"1080p": 1080, "preview": 360}.get(snapshot["resolution"], 720)
        width = height * 16 // 9
        if snapshot["aspect"] == "9:16":
            width, height = height, width
        segments = []
        total = 0
        for index, clip in enumerate(snapshot["clips"]):
            source = sources[clip["media_id"]]
            info = self.probe(source)
            start, end = clip["trim_in_ms"], clip["trim_out_ms"]
            if start < 0 or end > info["duration_ms"] or end <= start:
                raise RuntimeError("来源实际时长已变化，裁剪范围无效，请同步视频后重试")
            start_frame, end_frame = (start * 30 + 500) // 1000, (end * 30 + 500) // 1000
            frames = end_frame - start_frame
            if precise and frames < 1:
                raise RuntimeError("片段长度不足一帧，请调整裁剪范围")
            seconds = frames / 30 if precise else (end - start) / 1000
            total += seconds * 1000
            extension = "mov" if precise else "mp4"
            segment = directory / f"segment-{index}.{extension}"
            part = directory / f"segment-{index}.part.{extension}"
            signature = hashlib.sha256(
                json.dumps(
                    [clip, width, height, snapshot.get("version", 1), "source-frame-grid-v2"],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            record = directory / f"segment-{index}.json"
            cached = json.loads(record.read_text()) if record.exists() else {}
            if not (
                segment.exists()
                and cached.get("signature") == signature
                and cached.get("checksum") == checksum(segment)
            ):
                args = [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-protocol_whitelist",
                    "file,pipe",
                    *([] if precise else ["-ss", str(start / 1000)]),
                    "-i",
                    str(source),
                ]
                silent = clip["muted"] or not info["has_audio"]
                if silent:
                    args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                args += [
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0" if silent else "0:a:0",
                    "-t",
                    str(seconds),
                    "-vf",
                    # Normalize the original source on the same 30 fps grid as
                    # the browser proxy BEFORE trimming. Input seeking resets
                    # that grid and can choose a different frame for 24 fps media.
                    "fps=30,"
                    + (
                        "tpad=stop_mode=clone:stop_duration=0.04,"
                        f"trim=start_frame={start_frame}:end_frame={end_frame},"
                        if precise
                        else ""
                    )
                    + "setpts=PTS-STARTPTS,"
                    + f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
                    + f"force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                    "-af",
                    "aresample=48000:async=1:first_pts=0,apad"
                    + (
                        f",atrim=start_sample={start_frame * 1600}:"
                        f"end_sample={end_frame * 1600},asetpts=PTS-STARTPTS"
                        if precise
                        else ""
                    ),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-threads",
                    "2",
                    "-c:a",
                    "pcm_s16le" if precise else "aac",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    str(part),
                ]
                if precise:
                    args[-1:-1] = ["-bf", "0", "-video_track_timescale", "30000"]
                self.run(
                    args,
                    directory,
                    stage="rendering",
                    progress=10 + int(index / len(snapshot["clips"]) * 75),
                )
                part.replace(segment)
                record.write_text(
                    json.dumps({"signature": signature, "checksum": checksum(segment)})
                )
            segments.append(segment)
        listing = directory / "concat.txt"
        listing.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="ascii")
        output = directory / "output.mp4"
        part = directory / "output.part.mp4"
        self.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(listing),
                "-c:v",
                "copy",
                "-c:a",
                "aac" if precise else "copy",
                *(["-b:a", "192k", "-t", str(total / 1000)] if precise else []),
                "-movflags",
                "+faststart",
                str(part),
            ],
            directory,
            stage="joining",
            progress=88,
        )
        if snapshot.get("sound"):
            from .sound_render import mix_sound

            mix_sound(self, part, snapshot["sound"], sources, directory, total, width, height)
        metadata = self.probe(part)
        if (
            (metadata["width"], metadata["height"]) != (width, height)
            or not metadata["has_audio"]
            or abs(metadata["duration_ms"] - total)
            > (40 if precise else max(200, len(segments) * 70))
        ):
            raise RuntimeError("导出文件校验失败，请重试")
        part.replace(output)
        return output, metadata
