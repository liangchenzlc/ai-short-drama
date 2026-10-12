"""Reclaim abandoned creation bytes without losing their retry identity."""

import logging
from collections.abc import Iterator

from sqlalchemy import select

from short_drama.dao.canvas_creation_dao import CanvasCreationDAO
from short_drama.domain import CanvasBinaryResource, MediaFile
from short_drama.storage.models import ObjectLocation

from .base import utcnow
from .canvas_resource_copy import canvas_copy_lock

logger = logging.getLogger(__name__)


def _expired_creation_ids(factory) -> Iterator[int]:
    cursor, cutoff = 0, utcnow()
    while True:
        with factory() as session:
            identifiers = CanvasCreationDAO(session).expired(cutoff, 100, after_id=cursor)
        if not identifiers:
            return
        # 回收会更新updated_at，失败项仍保留；稳定ID保证一轮内每项只尝试一次。
        cursor = identifiers[-1]
        yield from identifiers


def cleanup_canvas_creations(factory, storage, settings, *, apply=False):
    buckets = {
        settings.minio_image_bucket,
        settings.minio_video_bucket,
        settings.minio_audio_bucket,
    }
    with factory() as session:
        engine = session.get_bind().engine
    examined = removed = failed = 0
    for identifier in _expired_creation_ids(factory):
        try:
            # The same mutex serializes the entire create, including I/O and commit.
            with canvas_copy_lock(engine, identifier):
                with factory.begin() as session:
                    dao = CanvasCreationDAO(session)
                    attempt = dao.attempt(identifier)
                    if (
                        attempt is None
                        or attempt.status != "pending"
                        or attempt.expires_at > utcnow()
                    ):
                        continue
                    files = dao.resources(identifier)
                    locations = []
                    for row in files:
                        if row.status == "attached":
                            raise ValueError("Pending creation contains an attached file")
                        if any(
                            session.scalar(
                                select(model.id)
                                .where(model.storage_locator == row.storage_locator)
                                .limit(1)
                            )
                            is not None
                            for model in (MediaFile, CanvasBinaryResource)
                        ):
                            raise ValueError(
                                "Creation bytes are already retained by canonical media"
                            )
                        location = ObjectLocation.parse(row.storage_locator, buckets)
                        if location.object_name != f"canvas/resources/{row.target_resource_id}":
                            raise ValueError("Creation file is outside its reserved namespace")
                        locations.append(location)
                    if apply:
                        # Invalidate acknowledged staging before deleting bytes.
                        # A crash after removal must force a fresh copy on retry.
                        for row in files:
                            row.source_media_id = row.source_binary_id = row.copied_at = None
                            row.status = "pending"
                            row.released_at = row.updated_at = utcnow()
                        attempt.updated_at = utcnow()
                examined += 1
                if not apply:
                    continue
                # No database row transaction spans object-store I/O. If deletion
                # or the following commit fails, the original identity is retried.
                for location in locations:
                    storage.remove(location.bucket, location.object_name)
                    removed += 1
        except Exception as error:
            failed += 1
            logger.warning(
                "Canvas creation cleanup failed: attempt=%s error=%s",
                identifier,
                type(error).__name__,
            )
    return {"examined": examined, "remove_requests": removed, "failed": failed, "applied": apply}
