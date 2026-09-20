import re
from datetime import UTC, datetime
from typing import BinaryIO

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError
from short_drama.storage.minio import MinioStorage
from short_drama.storage.models import ObjectLocation, StoredObject
from short_drama.utils.snowflake import next_id

_MIME = re.compile(r"^(image|video)/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/mpeg": ".mpeg",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
}


class StorageService:
    """Storage primitives; callers own media metadata and reference-aware deletion."""

    def __init__(self, storage: MinioStorage, settings: Settings):
        self.storage = storage
        self.settings = settings
        self._buckets = {"image": settings.minio_image_bucket, "video": settings.minio_video_bucket}

    def _location(self, locator: str) -> ObjectLocation:
        return ObjectLocation.parse(locator, allowed_buckets=set(self._buckets.values()))

    def check_storage(self) -> dict[str, str]:
        return self.storage.check_buckets()

    def upload(self, data: BinaryIO, *, length: int, content_type: str) -> StoredObject:
        if type(length) is not int or not 0 <= length <= 5 * 1024**4:
            raise BusinessError("Upload length must be a nonnegative integer of at most 5 TiB")
        match = _MIME.fullmatch(content_type) if isinstance(content_type, str) else None
        if match is None or len(content_type) > 127:
            raise BusinessError("Only canonical image or video MIME types are supported")
        if not callable(getattr(data, "read", None)):
            raise BusinessError("Upload requires a binary stream")
        bucket = self._buckets[match.group(1)]
        key = f"{datetime.now(UTC):%Y/%m/%d}/{next_id()}{_EXTENSIONS.get(content_type, '')}"
        return self.storage.put(bucket, key, data, length, content_type)

    def stat(self, locator: str) -> StoredObject:
        location = self._location(locator)
        return self.storage.stat(location.bucket, location.object_name)

    def open(self, locator: str):
        """Use as a context manager; stream with response.stream(chunk_size)."""
        location = self._location(locator)
        return self.storage.open(location.bucket, location.object_name)

    def delete(self, locator: str, *, version_id: str | None = None) -> None:
        """Physical deletion primitive; business callers must first check references.

        With versioning enabled, omit version_id for a delete marker or pass the
        returned upload version ID to remove that exact version.
        """
        location = self._location(locator)
        self.storage.remove(location.bucket, location.object_name, version_id=version_id)

    def download_url(self, locator: str, *, expires_seconds: int | None = None) -> str:
        location = self._location(locator)
        expiry = self.settings.minio_presign_expiry if expires_seconds is None else expires_seconds
        if type(expiry) is not int or not 1 <= expiry <= 604800:
            raise BusinessError("Signed URL expiry must be an integer between 1 and 604800 seconds")
        return self.storage.presigned_get(location.bucket, location.object_name, expiry)
