"""Drain durable canvas deletion jobs; failed/unknown removes remain retryable."""

import logging
from datetime import timedelta

from sqlalchemy import or_, select

from short_drama.domain import (
    CanvasBinaryResource,
    CanvasCreationAttempt,
    CanvasCreationResource,
    CanvasResourceDeletion,
    MediaFile,
)
from short_drama.domain.canvas.document.canvas import CanvasWriteReceipt
from short_drama.service.base import utcnow
from short_drama.storage.models import ObjectLocation

logger = logging.getLogger(__name__)


def _retained(session, locator: str, resource_id: int | None = None) -> bool:
    for model in (MediaFile, CanvasBinaryResource):
        predicate = model.storage_locator == locator
        if resource_id is not None:
            predicate = or_(predicate, model.id == resource_id)
        if session.scalar(select(model.id).where(predicate).limit(1).with_for_update()) is not None:
            return True
    return False


def _remove(session, job, storage, settings) -> bool:
    location = ObjectLocation.parse(
        job.storage_locator,
        {
            settings.minio_image_bucket,
            settings.minio_video_bucket,
            settings.minio_audio_bucket,
        },
    )
    if location.object_name != f"canvas/resources/{job.resource_id}":
        raise ValueError("Deletion object is outside its retired canvas resource namespace")
    if job.creation_resource_id is not None:
        prepared = session.get(CanvasCreationResource, job.creation_resource_id)
        disposal = session.get(CanvasWriteReceipt, job.disposal_receipt_id)
        attempt = session.get(CanvasCreationAttempt, prepared.attempt_id) if prepared else None
        if (
            prepared is None
            or disposal is None
            or attempt is None
            or prepared.user_id != job.user_id
            or prepared.created_by != job.user_id
            or prepared.target_resource_id != job.resource_id
            or prepared.storage_locator != job.storage_locator
            or prepared.status != "attached"
            or prepared.released_at is None
            or prepared.source_media_id is not None
            or prepared.source_binary_id is not None
            or attempt.user_id != job.user_id
            or attempt.created_by != job.user_id
            or attempt.status != "ready"
            or disposal.actor_user_id != job.user_id
            or disposal.operation_kind != "canvas.recycle.purge"
            or attempt.result_json.get("project_id") != str(disposal.project_id)
            or attempt.result_json.get("id") != str(disposal.canvas_id)
            or attempt.result_json.get("source_key") != disposal.result_json.get("source_key")
            or attempt.result_json.get("resource_map", {}).get(str(prepared.source_resource_id))
            != str(job.resource_id)
        ):
            raise ValueError("Deletion no longer identifies its disposed creation copy")
        public = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.actor_user_id == job.user_id,
                CanvasWriteReceipt.idempotency_key == attempt.idempotency_key,
                CanvasWriteReceipt.operation_kind == attempt.operation_kind,
                CanvasWriteReceipt.project_id == disposal.project_id,
                CanvasWriteReceipt.canvas_id == disposal.canvas_id,
                CanvasWriteReceipt.request_hash == attempt.request_hash,
            )
        )
        if public is None or public.result_json != attempt.result_json:
            raise ValueError("Deletion requires its unchanged public creation receipt")
    # Canonical records protect all tenants and archived projects. The worker uses
    # an internal session, never an HTTP-controlled bypass of account scoping.
    retained = _retained(session, job.storage_locator, job.resource_id)
    for index in range(job.chunk_count):
        chunk = ObjectLocation(location.bucket, f"canvas/uploads/{job.upload_id}/{index}")
        if _retained(session, chunk.locator):
            retained = True
        else:
            storage.remove(chunk.bucket, chunk.object_name)
    if not retained:
        storage.remove(location.bucket, location.object_name)
    return retained


def cleanup_canvas_resources(factory, storage, settings, *, limit: int = 32, apply=False):
    if not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be between 1 and 1000")
    result = {"examined": 0, "completed": 0, "retained": 0, "failed": 0, "applied": apply}
    cursor = 0
    for _ in range(limit):
        with factory.begin() as session:
            if session.info.get("actor"):
                raise ValueError("Resource cleanup requires an internal maintenance session")
            query = (
                select(CanvasResourceDeletion)
                .where(
                    CanvasResourceDeletion.status == "pending",
                    CanvasResourceDeletion.next_attempt_at <= utcnow(),
                    CanvasResourceDeletion.id > cursor,
                )
                .order_by(CanvasResourceDeletion.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            job = session.scalar(query)
            if job is None:
                break
            cursor = job.id
            result["examined"] += 1
            if not apply:
                continue
            job.attempts += 1
            try:
                retained = _remove(session, job, storage, settings)
            except Exception as error:
                # The exact object and receipt survive even after a successful
                # remove with a lost response. MinIO remove is safe to repeat.
                job.error_code = "canvas_resource_remove_failed"
                job.next_attempt_at = utcnow() + timedelta(
                    seconds=min(3600, 2 ** min(job.attempts, 10) * 5)
                )
                result["failed"] += 1
                logger.warning(
                    "Canvas resource deletion deferred: job=%s resource=%s error=%s",
                    job.id,
                    job.resource_id,
                    type(error).__name__,
                )
            else:
                job.status = "retained" if retained else "completed"
                job.completed_at = utcnow()
                job.error_code = "canvas_resource_storage_shared" if retained else None
                result[job.status] += 1
            job.updated_at = utcnow()
    return result
