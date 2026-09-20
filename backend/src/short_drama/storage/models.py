from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, unquote, urlsplit

from pydantic import BaseModel

from short_drama.core.exceptions import BusinessError


@dataclass(frozen=True)
class ObjectLocation:
    bucket: str
    object_name: str

    def __post_init__(self):
        if (
            not self.object_name.strip()
            or "\\" in self.object_name
            or any(ord(char) < 32 or ord(char) == 127 for char in self.object_name)
            or any(part in ("", ".", "..") for part in self.object_name.split("/"))
        ):
            raise BusinessError("Invalid storage object name")
        if len(self.locator) > 700 or len(self.object_name.encode("utf-8")) > 1024:
            raise BusinessError("Storage locator is too long")

    @property
    def locator(self) -> str:
        return f"minio://{self.bucket}/{quote(self.object_name, safe='/')}"

    @classmethod
    def parse(cls, locator: str, allowed_buckets: set[str]) -> "ObjectLocation":
        if not isinstance(locator, str) or len(locator) > 700:
            raise BusinessError("Invalid storage locator")
        try:
            parsed = urlsplit(locator)
            if (
                parsed.scheme != "minio"
                or parsed.netloc not in allowed_buckets
                or parsed.query
                or parsed.fragment
                or "?" in locator
                or "#" in locator
            ):
                raise ValueError
            key = unquote(parsed.path.removeprefix("/"), errors="strict")
            location = cls(parsed.netloc, key)
            # Reject aliases, encoded slashes, bad %-escapes and silent URL normalization.
            if location.locator != locator:
                raise ValueError
            return location
        except (ValueError, UnicodeError):
            raise BusinessError("Invalid storage locator") from None


class StoredObject(BaseModel):
    bucket: str
    object_name: str
    storage_locator: str
    size: int
    content_type: str
    etag: str | None = None
    version_id: str | None = None
    last_modified: datetime | None = None
