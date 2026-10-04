from sqlalchemy import select

from short_drama.core.exceptions import BusinessError
from short_drama.domain import AgentAttachment, MediaFile
from short_drama.schemas import MediaFileCreate, MediaFileRead, MediaFileUpdate

from .base import BaseService


class MediaFileService(BaseService):
    model = MediaFile
    create_schema = MediaFileCreate
    update_schema = MediaFileUpdate
    read_schema = MediaFileRead

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
