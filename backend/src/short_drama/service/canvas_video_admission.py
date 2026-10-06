"""冻结已授权的视频参考身份与真实元信息，不扩大标准模式 DTO。"""

import re
from urllib.parse import urlsplit

from short_drama.ai.canvas_video_adapters import VIDEO_ADAPTERS as VIDEO_ADAPTERS
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.access import scope_of


def _https_reference(value: str) -> bool:
    try:
        parts = urlsplit(value)
        return bool(
            parts.scheme == "https"
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and not parts.fragment
            and "\\" not in value
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and (parts.port is None or 1 <= parts.port <= 65535)
        )
    except ValueError:
        return False


def freeze_video_references(
    generation, prepared: dict, project_id: int, adapter: str, *, channel_key="", model=""
) -> None:
    request = prepared["canvas_request"]["input"]
    allows_inline = channel_key == "beefapi" and model.lower() == "wan3.0-video"
    for kind, group, field in (
        ("image", "referenceImages", "reference_media_ids"),
        ("video", "referenceVideos", "video_reference_media_ids"),
        ("audio", "referenceAudios", "audio_reference_media_ids"),
    ):
        identifiers = []
        for reference in request.get(group, []):
            storage_key = reference.get("storageKey") or ""
            match = re.fullmatch(r"resource:([1-9][0-9]*)", storage_key)
            if storage_key and match is None:
                raise WorkflowError(
                    "canvas_reference_not_saved", "参考媒体必须先保存为稳定资源", 422
                )
            if match is None:
                if not _https_reference(reference.get("url") or ""):
                    raise WorkflowError(
                        "reference_media_requires_url", "参考媒体需要有效的 HTTPS 链接", 422
                    )
                continue
            if adapter == "canvas_newapi_video_generations.v1" and not allows_inline:
                raise WorkflowError(
                    "reference_media_requires_url",
                    "该渠道需要参考媒体的 HTTPS 链接，尚未创建生成任务",
                    422,
                )
            identifier = match.group(1)
            media = generation._validate_media(identifier, kind)
            if scope_of(generation.session, media) != (None, project_id):
                raise NotFound("参考媒体不存在或不属于当前项目")
            if not media.storage_locator.startswith("minio://"):
                raise WorkflowError(
                    "canvas_reference_not_saved", "参考媒体必须先保存为永久资源", 422
                )
            identifiers.append(identifier)
            reference.pop("url", None)
            reference["type"] = media.format_code
            for name, value in (
                ("bytes", media.byte_size),
                ("width", media.width),
                ("height", media.height),
                ("durationMs", media.duration_ms),
            ):
                reference.pop(name, None)
                if value is not None:
                    reference[name] = value
        prepared["input"][field] = identifiers
