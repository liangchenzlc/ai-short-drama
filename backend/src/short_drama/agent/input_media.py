"""Read only frozen managed inputs. No SDK URL fetches or arbitrary external I/O."""

import base64
import hashlib
import io
import math
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps

from short_drama.core.exceptions import WorkflowError
from short_drama.service.asset_image_service import inspect_image_upload
from short_drama.service.audio_media import inspect_audio
from short_drama.service.storage_service import StorageService
from short_drama.service.video_render import VideoRenderer, executable
from short_drama.storage.minio import MinioStorage

MAX_INPUT_BYTES = {
    "text": 1024**2,
    "image": 20 * 1024**2,
    "audio": 20 * 1024**2,
    "video": 50 * 1024**2,
}
MAX_PROCESSED_BYTES = 4 * 1024**2
MAX_DURATION_MS = 120000


def inspect_attachment(data, filename, settings):
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        if not 0 < len(data) <= MAX_INPUT_BYTES["text"]:
            raise ValueError("文本附件须为 1 MiB 以内的 TXT 或 Markdown")
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = data.decode("gb18030")
        if not content.strip() or "\x00" in content:
            raise ValueError("文本附件须包含有效正文")
        return "text", "text/plain", content, {}
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        inspected = inspect_image_upload(io.BytesIO(data))
        try:
            return (
                "image",
                inspected.content_type,
                None,
                {
                    "width": inspected.width,
                    "height": inspected.height,
                },
            )
        finally:
            inspected.stream.close()
    if suffix not in {".mp3", ".wav", ".m4a", ".mp4", ".webm"}:
        raise ValueError("支持 TXT/Markdown、PNG/JPEG/WebP、MP4/WebM、MP3/WAV/M4A")
    kind = "audio" if suffix in {".mp3", ".wav", ".m4a"} else "video"
    if not 0 < len(data) <= MAX_INPUT_BYTES[kind]:
        raise ValueError("音频附件最多 20 MiB，视频附件最多 50 MiB")
    with TemporaryDirectory(prefix="agent-inspect-") as directory:
        path = Path(directory) / f"input{suffix}"
        path.write_bytes(data)
        if kind == "audio":
            metadata = inspect_audio(path, settings)
            mime = metadata.pop("mime")
        else:
            if data[4:8] == b"ftyp":
                mime = "video/mp4"
            elif data.startswith(b"\x1a\x45\xdf\xa3"):
                mime = "video/webm"
            else:
                raise ValueError("视频容器须为 MP4 或 WebM")
            metadata = VideoRenderer(settings).probe(path)
        if metadata["duration_ms"] > MAX_DURATION_MS:
            raise ValueError("Agent 音频和视频附件最长为 120 秒")
        return kind, mime, None, metadata


def _binary(data, mime):
    return {"kind": "binary", "data": base64.urlsafe_b64encode(data).decode(), "media_type": mime}


def _image(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 40000000:
            raise ValueError("图片像素数超过 4000 万")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1280, 1280))
        target = io.BytesIO()
        image.save(target, "JPEG", quality=80)
        return _binary(target.getvalue(), "image/jpeg")


def _ffmpeg(settings, path, extra):
    result = subprocess.run(
        [
            executable(settings.render_ffmpeg_path),
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            *extra,
        ],
        capture_output=True,
        timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode or not result.stdout or len(result.stdout) > MAX_PROCESSED_BYTES:
        raise ValueError("附件无法解码或处理结果超过上下文限制")
    return result.stdout


def materialize_prompt(prompt, settings, storage=None):
    """Convert trusted checkpoint locators to bounded inlined SDK binary parts."""
    if not isinstance(prompt, dict) or prompt.get("codec") != "agent.attachments":
        return prompt
    if all(item["kind"] == "text" for item in prompt["attachments"]):
        return _materialize_prompt(prompt, settings, None)
    if storage is not None:
        return _materialize_prompt(prompt, settings, storage)
    client = MinioStorage(settings)
    try:
        return _materialize_prompt(prompt, settings, StorageService(client, settings))
    finally:
        client.close()


def _materialize_prompt(prompt, settings, service):
    parts = [prompt["content"]]
    for attachment in prompt["attachments"]:
        kind, name = attachment["kind"], attachment["name"]
        if attachment.get("text_content"):
            parts.append(f"资料 {name}：\n{attachment['text_content']}")
        if kind == "text":
            continue
        try:
            with service.open(attachment["storage_locator"]) as response:
                data = response.read(MAX_INPUT_BYTES[kind] + 1)
            if len(data) > MAX_INPUT_BYTES[kind] or not data:
                raise ValueError("附件大小超过限制")
            if hashlib.sha256(data).hexdigest() != attachment["checksum_sha256"]:
                raise ValueError("附件文件校验失败")
            parts.append(f"附件 {name}：")
            if kind == "image":
                parts.append(_image(data))
            else:
                with TemporaryDirectory(prefix="agent-context-") as directory:
                    path = Path(directory) / "input"
                    path.write_bytes(data)
                    if kind == "video":
                        metadata = VideoRenderer(settings).probe(path)
                        if metadata["duration_ms"] > MAX_DURATION_MS:
                            raise ValueError("视频附件超过 120 秒")
                        count = min(8, max(1, math.ceil(metadata["duration_ms"] / 15000)))
                        duration = metadata["duration_ms"] / 1000
                        parts.append(f"视频画面采用 {count} 张均匀采样帧，只能判断这些采样画面。")
                        for index in range(count):
                            moment = duration * (index + 0.5) / count
                            frame = _ffmpeg(
                                settings,
                                path,
                                [
                                    "-ss",
                                    str(moment),
                                    "-frames:v",
                                    "1",
                                    "-vf",
                                    "scale=1024:1024:force_original_aspect_ratio=decrease",
                                    "-f",
                                    "image2pipe",
                                    "-vcodec",
                                    "mjpeg",
                                    "pipe:1",
                                ],
                            )
                            parts += [f"采样时间 {moment:.1f} 秒", _image(frame)]
                        if metadata["has_audio"] and prompt["video_audio"] == "visual_only":
                            parts.append("用户明确只分析视频画面，本次不理解视频声音。")
                        elif metadata["has_audio"]:
                            parts.append(
                                _binary(
                                    _ffmpeg(
                                        settings,
                                        path,
                                        [
                                            "-vn",
                                            "-t",
                                            "120",
                                            "-ac",
                                            "1",
                                            "-ar",
                                            "16000",
                                            "-b:a",
                                            "48k",
                                            "-f",
                                            "mp3",
                                            "pipe:1",
                                        ],
                                    ),
                                    "audio/mpeg",
                                )
                            )
                    else:
                        metadata = inspect_audio(path, settings)
                        if metadata["duration_ms"] > MAX_DURATION_MS:
                            raise ValueError("音频附件超过 120 秒")
                        parts.append(
                            _binary(
                                _ffmpeg(
                                    settings,
                                    path,
                                    [
                                        "-vn",
                                        "-t",
                                        "120",
                                        "-ac",
                                        "1",
                                        "-ar",
                                        "16000",
                                        "-b:a",
                                        "48k",
                                        "-f",
                                        "mp3",
                                        "pipe:1",
                                    ],
                                ),
                                "audio/mpeg",
                            )
                        )
        except Exception as error:
            raise WorkflowError(
                "agent_attachment_unavailable", f"无法读取或处理附件“{name}”，请核对文件后重试", 422
            ) from error
    size = sum(
        len(base64.urlsafe_b64decode(item["data"])) for item in parts if isinstance(item, dict)
    )
    if size > MAX_PROCESSED_BYTES:
        raise WorkflowError("agent_context_too_large", "处理后的媒体超过 4 MiB，请减少附件", 422)
    return parts
