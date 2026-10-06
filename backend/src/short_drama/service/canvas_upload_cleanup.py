"""Retryable cleanup of committed/expired upload staging objects, never published media."""

import logging
from contextlib import nullcontext

from sqlalchemy import or_, select

from short_drama.domain import (
    CanvasBinaryResource,
    CanvasResourceChunk,
    CanvasResourceCopySource,
    CanvasResourceUpload,
    MediaFile,
)
from short_drama.storage.models import ObjectLocation

from .base import utcnow
from .canvas_resource_copy import canvas_copy_lock, release_copy_source

logger = logging.getLogger(__name__)


def cleanup_canvas_uploads(factory, storage, settings, *, apply=False):
    """Internal maintenance only; recheck each durable upload under its write lock."""
    buckets = {
        settings.minio_image_bucket,
        settings.minio_video_bucket,
        settings.minio_audio_bucket,
    }
    cursor, examined, removed, failed = 0, 0, 0, 0
    while True:
        with factory() as session:
            engine = session.get_bind().engine
            identifiers = list(
                session.execute(
                    select(CanvasResourceUpload.id, CanvasResourceUpload.mode)
                    .where(
                        CanvasResourceUpload.id > cursor,
                        or_(
                            CanvasResourceUpload.status == "ready",
                            CanvasResourceUpload.expires_at < utcnow(),
                        ),
                    )
                    .order_by(CanvasResourceUpload.id)
                    .limit(50)
                )
            )
        if not identifiers:
            break
        for identifier, mode in identifiers:
            cursor = identifier
            try:
                mutex = canvas_copy_lock(engine, identifier) if mode == "copy" else nullcontext()
                with mutex, factory.begin() as session:
                    upload = session.scalar(
                        select(CanvasResourceUpload)
                        .where(CanvasResourceUpload.id == identifier)
                        .with_for_update()
                    )
                    if upload is None or upload.status != "ready" and upload.expires_at >= utcnow():
                        continue
                    examined += 1
                    chunks = list(
                        session.scalars(
                            select(CanvasResourceChunk).where(
                                CanvasResourceChunk.upload_id == identifier
                            )
                        )
                    )
                    for part in chunks:
                        location = ObjectLocation.parse(part.storage_locator, buckets)
                        if location.object_name != f"canvas/uploads/{upload.id}/{part.chunk_index}":
                            raise ValueError("Upload chunk is outside its staging namespace")
                        if apply:
                            storage.remove(location.bucket, location.object_name)
                            removed += 1
                            if upload.status == "pending":
                                session.delete(part)
                    if upload.status != "pending":
                        continue
                    # A failed SQL commit may have left the final bytes. Never delete a
                    # locator now retained by a canonical media or binary record.
                    retained = any(
                        session.scalar(
                            select(model.id)
                            .where(model.storage_locator == upload.storage_locator)
                            .limit(1)
                        )
                        is not None
                        for model in (MediaFile, CanvasBinaryResource)
                    )
                    location = ObjectLocation.parse(upload.storage_locator, buckets)
                    if location.object_name != f"canvas/resources/{upload.reserved_resource_id}":
                        raise ValueError("Upload resource is outside its reserved namespace")
                    if apply and not retained:
                        storage.remove(location.bucket, location.object_name)
                        removed += 1
                        if upload.mode == "copy":
                            source = session.scalar(
                                select(CanvasResourceCopySource)
                                .where(CanvasResourceCopySource.upload_id == upload.id)
                                .with_for_update()
                            )
                            if source is not None:
                                release_copy_source(source)
            except Exception as error:
                failed += 1
                logger.warning(
                    "Canvas upload cleanup failed: upload=%s error=%s",
                    identifier,
                    type(error).__name__,
                )
    return {"examined": examined, "remove_requests": removed, "failed": failed, "applied": apply}
