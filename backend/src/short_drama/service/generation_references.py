"""Read persisted reference media through configured storage, never a caller URL."""

import time

from short_drama.ai import GenerationError
from short_drama.core.exceptions import BusinessError, NotFound
from short_drama.domain import MediaFile

from .storage_service import StorageService


class StoredImageReferences:
    def __init__(self, factory, storage, settings, media_ids):
        self.storage = StorageService(storage, settings)
        self.locators = []
        with factory() as session:
            for identifier in media_ids:
                media = session.get(MediaFile, int(identifier))
                if media is None:
                    raise GenerationError("reference_missing")
                self.locators.append(media.storage_locator)

    def __call__(self, index, max_bytes, deadline):
        def check_deadline():
            if time.monotonic() >= deadline:
                raise GenerationError("timeout")

        check_deadline()
        try:
            locator = self.locators[index]
            if self.storage.stat(locator).size > max_bytes:
                raise GenerationError("reference_images_too_large")
            check_deadline()
            data = bytearray()
            with self.storage.open(locator) as response:
                for chunk in response.stream(64 * 1024):
                    check_deadline()
                    if len(data) + len(chunk) > max_bytes:
                        raise GenerationError("reference_images_too_large")
                    data.extend(chunk)
            check_deadline()
            return bytes(data)
        except NotFound:
            raise GenerationError("reference_missing") from None
        except BusinessError:
            raise GenerationError("reference_storage_unavailable") from None
