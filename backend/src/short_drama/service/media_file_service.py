from short_drama.core.exceptions import BusinessError
from short_drama.domain import MediaFile
from short_drama.schemas import MediaFileCreate, MediaFileRead, MediaFileUpdate

from .base import BaseService


class MediaFileService(BaseService):
    model = MediaFile
    create_schema = MediaFileCreate
    update_schema = MediaFileUpdate
    read_schema = MediaFileRead

    def _validate_update(self, entity, values):
        if values.get("duration_ms") is not None and not entity.format_code.startswith("video/"):
            raise BusinessError("Only video media may have duration_ms")
