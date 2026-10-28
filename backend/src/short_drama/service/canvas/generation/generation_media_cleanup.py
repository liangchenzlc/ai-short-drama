"""处置作者的生成媒体原子退役及独立字节回收；其他实际引用继续保留。"""

import logging
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from short_drama.core.config import Settings
from short_drama.dao.canvas.generation.generation_media_cleanup import (
    CanvasGenerationMediaCleanupDAO,
)
from short_drama.dao.canvas.resources.resource_deletion import CanvasResourceDeletionDAO
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasGenerationMediaDeletion,
    MediaAsset,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.canvas.document.canvas import CanvasWriteReceipt
from short_drama.service.base import utcnow
from short_drama.service.canvas.resources.resource_cleanup import _retained
from short_drama.storage.minio import MinioStorage
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id

logger = logging.getLogger(__name__)


def _origin(
    record: AIGenerationRecord,
    task_id: int,
    asset_id: int,
    output_index: int,
    media_type: str,
    checksum: str,
    extension: str,
    locator: str,
    settings: Settings,
) -> tuple[ObjectLocation, dict]:
    if not re.fullmatch(r"[0-9a-f]{64}", checksum) or not re.fullmatch(
        r"[a-z0-9]{1,12}", extension
    ):
        raise ValueError("Generation retirement has invalid checksum or extension")
    entries = [
        item
        for item in (record.response_data or {}).get("media_manifest", [])
        if item.get("asset_id") == str(asset_id) and item.get("output_index") == output_index
    ]
    if len(entries) != 1:
        raise ValueError("Generation retirement does not identify one archived output")
    entry = entries[0]
    if (
        record.task_id != task_id
        or entry.get("saved") is not True
        or entry.get("ignored") is True
        or entry.get("media_type") != media_type
        or entry.get("checksum") != checksum
        or entry.get("ext") != extension
        or entry.get("locator") != locator
    ):
        raise ValueError("Generation retirement conflicts with its retained archive evidence")
    buckets = {
        "image": settings.minio_image_bucket,
        "video": settings.minio_video_bucket,
        "audio": settings.minio_audio_bucket,
    }
    location = ObjectLocation.parse(locator, set(buckets.values()))
    if (
        location.bucket != buckets.get(media_type)
        or location.object_name
        != f"generations/{task_id}/{record.id}/{asset_id}-{checksum}.{extension}"
    ):
        raise ValueError("Generation retirement is outside its exact archived output namespace")
    return location, entry


def _retire(
    session: Session,
    dao: CanvasGenerationMediaCleanupDAO,
    canvas: ProjectCanvas,
    receipt: CanvasWriteReceipt,
    settings: Settings,
    apply: bool,
) -> tuple[int, int]:
    eligible = retained = 0
    references = CanvasResourceDeletionDAO(session)
    for record in dao.records(canvas, receipt.actor_user_id):
        assets = list(
            session.scalars(
                select(MediaAsset)
                .where(MediaAsset.record_id == record.id)
                .order_by(MediaAsset.id)
                .with_for_update()
            )
        )
        for asset in assets:
            media = session.scalar(
                select(MediaFile).where(MediaFile.id == asset.media_id).with_for_update()
            )
            if (
                media is None
                or media.created_by != receipt.actor_user_id
                or media.project_id != canvas.project_id
                or media.scope_user_id is not None
            ):
                retained += 1
                continue
            manifest = (record.response_data or {}).get("media_manifest", [])
            entry = next((item for item in manifest if item.get("asset_id") == str(asset.id)), {})
            _, entry = _origin(
                record,
                record.task_id,
                asset.id,
                asset.output_index,
                asset.media_type,
                media.checksum_sha256 or "",
                entry.get("ext", ""),
                media.storage_locator,
                settings,
            )
            if any(
                entry.get(field) != getattr(media, attr)
                for field, attr in (
                    ("mime", "format_code"),
                    ("byte_size", "byte_size"),
                    ("width", "width"),
                    ("height", "height"),
                    ("duration_ms", "duration_ms"),
                )
            ):
                raise ValueError(
                    "Generation retirement metadata does not match its canonical media"
                )
            results = dao.results(canvas, receipt.actor_user_id, record.task_id, media.id)
            released = {"media_assets": [asset.id], "canvas_results": [item.id for item in results]}
            if (
                references.referenced(media, released=released)
                or references.referenced(media, history=True)
                or references.shared_by_library(media, 0)
            ):
                retained += 1
                continue
            eligible += 1
            if not apply:
                continue
            now = utcnow()
            session.add(
                CanvasGenerationMediaDeletion(
                    id=next_id(),
                    project_id=canvas.project_id,
                    canvas_id=canvas.id,
                    user_id=receipt.actor_user_id,
                    created_by=receipt.actor_user_id,
                    updated_by=receipt.actor_user_id,
                    created_at=now,
                    updated_at=now,
                    purge_receipt_id=receipt.id,
                    task_id=record.task_id,
                    record_id=record.id,
                    asset_id=asset.id,
                    resource_id=media.id,
                    output_index=asset.output_index,
                    media_type=asset.media_type,
                    checksum_sha256=media.checksum_sha256,
                    extension=entry["ext"],
                    storage_locator=media.storage_locator,
                    status="pending",
                    attempts=0,
                    next_attempt_at=now,
                )
            )
            for item in results:
                session.delete(item)
            session.delete(asset)
            session.flush()
            session.delete(media)
            session.flush()
    return eligible, retained


def retire_canvas_generation_media(
    factory: sessionmaker[Session], settings: Settings, *, apply: bool = False
) -> dict[str, int | bool]:
    result = {
        "examined": 0,
        "eligible": 0,
        "retired": 0,
        "retained": 0,
        "failed": 0,
        "applied": apply,
    }
    cursor = 0
    while True:
        with factory() as session:
            candidates = CanvasGenerationMediaCleanupDAO(session).candidates(cursor)
        if not candidates:
            return result
        for receipt_id, project_id, canvas_id in candidates:
            cursor = receipt_id
            try:
                with factory.begin() as session:
                    dao = CanvasGenerationMediaCleanupDAO(session)
                    target = dao.lock_target(receipt_id, project_id, canvas_id)
                    if target is None:
                        continue
                    result["examined"] += 1
                    eligible, retained = _retire(session, dao, *target, settings, apply)
                result["eligible"] += eligible
                result["retained"] += retained
                if apply:
                    result["retired"] += eligible
            except Exception as error:
                result["failed"] += 1
                logger.warning(
                    "Canvas generation media retirement deferred: receipt=%s canvas=%s error=%s",
                    receipt_id,
                    canvas_id,
                    type(error).__name__,
                )


def _remove(
    session: Session,
    dao: CanvasGenerationMediaCleanupDAO,
    job: CanvasGenerationMediaDeletion,
    storage: MinioStorage,
    settings: Settings,
) -> bool:
    target = dao.lock_target(job.purge_receipt_id, job.project_id, job.canvas_id)
    record = session.scalar(
        select(AIGenerationRecord).where(AIGenerationRecord.id == job.record_id).with_for_update()
    )
    task = session.scalar(select(AsyncTask).where(AsyncTask.id == job.task_id).with_for_update())
    source = (record.request_data.get("source") or {}) if record else {}
    if (
        target is None
        or target[1].actor_user_id != job.user_id
        or task is None
        or record is None
        or task.project_id != job.project_id
        or task.scope_user_id is not None
        or task.initiated_by != job.user_id
        or task.status not in {"succeeded", "failed", "cancelled"}
        or task.lock_token is not None
        or task.next_action is not None
        or (task.error or {}).get("code") != "canvas_work_disposed"
        or record.status in {"sent", "unknown"}
        or source.get("canvas_id") != str(job.canvas_id)
        or source.get("project_id") != str(job.project_id)
        or source.get("scene") not in {"canvas_node", "canvas_tool"}
    ):
        raise ValueError("Generation deletion no longer identifies the disposed author's work")
    location, _ = _origin(
        record,
        job.task_id,
        job.asset_id,
        job.output_index,
        job.media_type,
        job.checksum_sha256,
        job.extension,
        job.storage_locator,
        settings,
    )
    if _retained(session, job.storage_locator, job.resource_id):
        return False
    storage.remove(location.bucket, location.object_name)
    return True


def cleanup_canvas_generation_media(
    factory: sessionmaker[Session],
    storage: MinioStorage,
    settings: Settings,
    *,
    limit: int = 32,
    apply: bool = False,
) -> dict[str, int | bool]:
    if not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be between 1 and 1000")
    result = {"examined": 0, "completed": 0, "retained": 0, "failed": 0, "applied": apply}
    cursor, cutoff = 0, utcnow()
    for _ in range(limit):
        with factory() as session:
            CanvasGenerationMediaCleanupDAO(session)
            candidate = session.scalar(
                select(CanvasGenerationMediaDeletion)
                .where(
                    CanvasGenerationMediaDeletion.id > cursor,
                    CanvasGenerationMediaDeletion.status == "pending",
                    CanvasGenerationMediaDeletion.next_attempt_at <= cutoff,
                )
                .order_by(CanvasGenerationMediaDeletion.id)
                .limit(1)
            )
            if candidate is None:
                return result
            identity = (
                candidate.id,
                candidate.purge_receipt_id,
                candidate.project_id,
                candidate.canvas_id,
            )
        cursor = identity[0]
        with factory.begin() as session:
            dao = CanvasGenerationMediaCleanupDAO(session)
            # 与作品处置/退役一致，先锁项目与画布，再锁删除任务。
            dao.lock_target(*identity[1:])
            job = session.scalar(
                select(CanvasGenerationMediaDeletion)
                .where(
                    CanvasGenerationMediaDeletion.id == cursor,
                    CanvasGenerationMediaDeletion.status == "pending",
                    CanvasGenerationMediaDeletion.next_attempt_at <= cutoff,
                )
                .with_for_update(skip_locked=True)
            )
            if job is None:
                continue
            result["examined"] += 1
            if not apply:
                continue
            job.attempts += 1
            try:
                removed = _remove(session, dao, job, storage, settings)
            except Exception as error:
                job.error_code = "canvas_generation_media_remove_failed"
                job.next_attempt_at = utcnow() + timedelta(
                    seconds=min(3600, 2 ** min(job.attempts, 10) * 5)
                )
                result["failed"] += 1
                logger.warning(
                    "Canvas generation media deletion deferred: job=%s resource=%s error=%s",
                    job.id,
                    job.resource_id,
                    type(error).__name__,
                )
            else:
                if removed:
                    job.status = "completed"
                    job.completed_at = utcnow()
                    job.error_code = None
                    result["completed"] += 1
                else:
                    # 另一账号的规范定位释放后仍可继续，不能永久完成为retained。
                    job.error_code = "canvas_generation_media_storage_shared"
                    job.next_attempt_at = utcnow() + timedelta(hours=1)
                    result["retained"] += 1
            job.updated_at = utcnow()
    return result
