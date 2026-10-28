"""Only compensate uploads after an independent durable-reference check."""

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import MediaFile
from short_drama.service.media.files.storage import StorageService
from short_drama.storage.models import StoredObject

logger = logging.getLogger(__name__)


def cleanup_unreferenced_uploads(
    session: Session, storage: StorageService, uploads: Iterable[StoredObject]
) -> None:
    uploads = list(uploads)
    if not uploads:
        return
    try:
        # The original connection may have lost COMMIT's acknowledgement. Use a
        # fresh system session: references in every account must protect bytes.
        with Session(session.get_bind().engine) as verification:
            retained = set(
                verification.scalars(
                    select(MediaFile.storage_locator)
                    .where(MediaFile.storage_locator.in_([row.storage_locator for row in uploads]))
                    .with_for_update()
                )
            )
    except Exception:
        logger.warning("Upload cleanup deferred: durable references could not be verified")
        return
    for stored in uploads:
        if stored.storage_locator not in retained:
            try:
                storage.delete(stored.storage_locator, version_id=stored.version_id)
            except Exception:
                logger.warning("Unreferenced upload cleanup failed; object retained")
