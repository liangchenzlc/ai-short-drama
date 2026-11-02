from sqlalchemy import select

from short_drama.core.exceptions import BusinessError
from short_drama.domain import AgentAttachment, MediaFile
from short_drama.schemas import MediaFileCreate, MediaFileRead, MediaFileUpdate
from short_drama.service.base import BaseService


class MediaFileService(BaseService):
    model = MediaFile
    create_schema = MediaFileCreate
    update_schema = MediaFileUpdate
    read_schema = MediaFileRead

    def record_upload_locked(self, stored, *, name, checksum, metadata):
        """Join the caller's authorized transaction after upload and content inspection."""
        values = self._payload(
            self.create_schema,
            {
                "format_code": stored.content_type,
                "storage_locator": stored.storage_locator,
                "original_name": name,
                "byte_size": stored.size,
                "checksum_sha256": checksum,
                "width": metadata.get("width"),
                "height": metadata.get("height"),
                "duration_ms": metadata.get("duration_ms"),
            },
        )
        if stored.content_type.startswith("video/"):
            values["video_metadata"] = metadata
        return self.dao.create(self._creation_audit(values))

    def _validate_update(self, entity, values):
        if values.get("duration_ms") is not None and not entity.format_code.startswith(
            ("video/", "audio/")
        ):
            raise BusinessError("Only video media may have duration_ms")

    def _before_delete(self, entity):
        # Deleted attachment metadata still belongs to message/run history.
        # The FK also protects references in other private conversations.
        if (
            self.session.scalar(
                select(AgentAttachment.id).where(AgentAttachment.media_id == entity.id).limit(1)
            )
            is not None
        ):
            raise BusinessError("Media is retained by an Agent conversation attachment")
