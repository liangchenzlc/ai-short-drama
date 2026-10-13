"""已采用样音的公共投影；不读取采用者的私人生成记录或凭据。"""

import re

from pydantic import ValidationError

from short_drama.core.exceptions import BusinessError, NotFound, WorkflowError
from short_drama.db.access import scope_of
from short_drama.domain import Asset, MediaFile
from short_drama.schemas.canvas_character_catalog import CanvasCharacterVoiceSampleRead

from .canvas_resource_service import CanvasResourceService


def read_canvas_voice_sample(catalog, settings, project_id: int, asset: Asset):
    if not getattr(settings, "native_video_enabled", False) or not getattr(
        settings, "audio_production_enabled", False
    ):
        return "unavailable", None
    binding = catalog.voice_binding(project_id, asset.id)
    if binding is None:
        return "missing", None
    media = catalog.media(binding.media_id)
    resources = CanvasResourceService(catalog.session, settings, None)
    if (
        not isinstance(media, MediaFile)
        or media.published_at is None
        or scope_of(catalog.session, media) != scope_of(catalog.session, asset)
    ):
        raise NotFound("角色已采用样音不存在或不属于角色范围")
    if (
        not re.fullmatch(r"audio/[a-zA-Z0-9!#$&^_.+\-]+", media.format_code)
        or not media.byte_size
        or not 0 < media.byte_size <= 10 * 1024**2
        or not media.duration_ms
        or not 3000 <= media.duration_ms <= 7500
        or not re.fullmatch(r"[0-9a-f]{64}", media.checksum_sha256 or "")
        or media.created_by is None
        or media.created_at is None
        or media.updated_at is None
    ):
        raise WorkflowError("canvas_character_voice_invalid", "角色样音缺少有效的归档元数据", 409)
    mapped = resources.resources.resource_map({media.id}, lock=True).get(media.id)
    if not isinstance(mapped, MediaFile):
        raise WorkflowError("canvas_character_resource_ambiguous", "角色声音资源标识存在歧义", 409)
    try:
        resources.location(media.storage_locator)
        sample = CanvasCharacterVoiceSampleRead(
            binding_version=binding.row_version,
            media_id=media.id,
            checksum_sha256=media.checksum_sha256,
            duration_ms=media.duration_ms,
            resource=resources.read_model(media),
        )
    except (BusinessError, ValidationError, TypeError, ValueError):
        raise WorkflowError(
            "canvas_character_voice_invalid", "角色样音定位或读取合同无效", 409
        ) from None
    return "ready", sample
