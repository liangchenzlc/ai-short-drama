"""按冻结引用的原始索引加载可信存储媒体，保留与 HTTPS 引用混排顺序。"""

import re
import time
from copy import deepcopy

from sqlalchemy.exc import SQLAlchemyError

from short_drama.ai import GenerationError
from short_drama.core.exceptions import BusinessError
from short_drama.domain import MediaFile
from short_drama.storage.models import ObjectLocation

from .generation_references import StoredImageReferences


class StoredCanvasVideoReferences:
    def __init__(self, factory, storage, settings, request: dict) -> None:
        self.loaders = {}
        self.positions = {}
        inputs = request["canvas_request"]["input"]
        for kind, group in (
            ("image", "referenceImages"),
            ("video", "referenceVideos"),
            ("audio", "referenceAudios"),
        ):
            identifiers, positions = [], {}
            for index, reference in enumerate(inputs.get(group, [])):
                match = re.fullmatch(r"resource:([1-9][0-9]*)", reference.get("storageKey") or "")
                if match is not None:
                    positions[index] = len(identifiers)
                    identifiers.append(match.group(1))
            if identifiers:
                self.loaders[kind] = StoredImageReferences(factory, storage, settings, identifiers)
                self.positions[kind] = positions

    def __call__(self, kind: str, index: int, max_bytes: int, deadline: float) -> bytes:
        position = self.positions.get(kind, {}).get(index)
        loader = self.loaders.get(kind)
        if position is None or loader is None:
            raise GenerationError("reference_missing")
        return loader(position, max_bytes, deadline)


class StoredCanvasVideoReferenceURLs:
    """第一次 submit 的临时 URL；与返回 bytes 的 loader 分开，禁止持久化签名。"""

    def __init__(self, factory, storage, settings, request: dict, project_id: int) -> None:
        self.factory, self.storage, self.settings = factory, storage, settings
        self.project_id = project_id
        self.references = {}
        for kind, group in (
            ("image", "referenceImages"),
            ("audio", "referenceAudios"),
        ):
            for index, reference in enumerate(request["canvas_request"]["input"].get(group, [])):
                match = re.fullmatch(r"resource:([1-9][0-9]*)", reference.get("storageKey") or "")
                if match is not None:
                    self.references[kind, index] = (int(match.group(1)), deepcopy(reference))

    def __call__(self, kind: str, index: int, max_bytes: int, deadline: float) -> str:
        def check_deadline():
            if time.monotonic() >= deadline:
                raise GenerationError("timeout")

        check_deadline()
        if type(self.project_id) is not int or self.project_id <= 0:
            raise GenerationError("reference_missing")
        reference = self.references.get((kind, index))
        if reference is None:
            raise GenerationError("reference_missing")
        identifier, frozen = reference
        try:
            with self.factory() as session:
                media = session.get(MediaFile, identifier)
                if media is None or (media.scope_user_id, media.project_id) != (
                    None,
                    self.project_id,
                ):
                    raise GenerationError("reference_missing")
                if not media.format_code.startswith(kind + "/") or any(
                    frozen.get(field) != actual
                    for field, actual in (
                        ("type", media.format_code),
                        ("bytes", media.byte_size),
                        ("width", media.width),
                        ("height", media.height),
                        ("durationMs", media.duration_ms),
                    )
                ):
                    raise GenerationError("reference_changed")
                if type(media.byte_size) is not int or not 0 < media.byte_size <= max_bytes:
                    raise GenerationError("reference_media_too_large")
                location = ObjectLocation.parse(
                    media.storage_locator,
                    allowed_buckets={getattr(self.settings, f"minio_{kind}_bucket")},
                )
                byte_size, mime = media.byte_size, media.format_code
            check_deadline()
            actual = self.storage.stat(location.bucket, location.object_name)
            if actual.size != byte_size or actual.content_type != mime:
                raise GenerationError("reference_changed")
            check_deadline()
            url = self.storage.provider_presigned_get(location.bucket, location.object_name)
            check_deadline()
            return url
        except BusinessError:
            raise GenerationError("reference_storage_unavailable") from None
        except SQLAlchemyError:
            raise GenerationError("reference_database_unavailable") from None
