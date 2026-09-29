"""Upload durable input images before an independent generation task exists."""

from .asset_image_service import AssetImageService
from .base import utcnow


class TaskReferenceService(AssetImageService):
    def upload(self, stream, length, name, content_type=None):
        result, _ = self._upload_image(None, stream, length, name, content_type)
        return result

    def _persist_upload(self, _owner_id, inspected, stored, original_name):
        with self._transaction():
            now = utcnow()
            media = self.media.create(
                {
                    "format_code": inspected.content_type,
                    "storage_locator": stored.storage_locator,
                    "original_name": (original_name or "参考图片")[:255],
                    "byte_size": inspected.byte_size,
                    "width": inspected.width,
                    "height": inspected.height,
                    "duration_ms": None,
                    "checksum_sha256": inspected.checksum_sha256,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            return {
                "media_id": str(media.id),
                "name": media.original_name,
                "url": self.storage.download_url(media.storage_locator),
                "width": media.width,
                "height": media.height,
            }, True
