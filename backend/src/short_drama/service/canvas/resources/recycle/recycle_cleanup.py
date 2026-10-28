"""处置后的整图载荷回收；上传字节由已有持久删除队列重试。"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from short_drama.core.config import Settings
from short_drama.dao.canvas.resources.recycle.recycle_cleanup import CanvasRecycleCleanupDAO
from short_drama.dao.canvas.resources.recycle.recycle_execution import CanvasRecycleExecutionDAO
from short_drama.dao.canvas.resources.resource_deletion import CanvasResourceDeletionDAO
from short_drama.dao.canvas.workspace.creation.creation import CanvasCreationDAO
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasCreationResource,
    CanvasResourceDeletion,
    CanvasResourceUpload,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.canvas.document.canvas import CanvasWriteReceipt
from short_drama.service.base import utcnow
from short_drama.service.canvas.resources.resource import CHUNK_SIZE
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id

logger = logging.getLogger(__name__)


def _retire_uploads(
    session: Session,
    canvas: ProjectCanvas,
    receipt: CanvasWriteReceipt,
    uploads: list[CanvasResourceUpload],
    settings: Settings,
) -> tuple[int, int]:
    deletions = CanvasResourceDeletionDAO(session)
    retired = retained = 0
    for upload in uploads:
        model = MediaFile if upload.media_id is not None else CanvasBinaryResource
        resource = session.scalar(
            select(model).where(model.id == upload.reserved_resource_id).with_for_update()
        )
        if resource is None or resource.storage_locator != upload.storage_locator:
            raise ValueError("Recycle upload no longer identifies its canonical resource")
        # 私人或其他作者的资源继续沿用原生命周期；项目归属不能代替上传作者。
        if (
            resource.created_by != receipt.actor_user_id
            or resource.project_id != canvas.project_id
            or resource.scope_user_id is not None
        ):
            retained += 1
            continue
        location = ObjectLocation.parse(
            upload.storage_locator,
            {
                settings.minio_image_bucket,
                settings.minio_video_bucket,
                settings.minio_audio_bucket,
            },
        )
        if location.object_name != f"canvas/resources/{resource.id}":
            raise ValueError("Recycle upload is outside its reserved namespace")
        if (
            deletions.referenced(resource)
            or deletions.referenced(resource, history=True)
            or deletions.shared_by_library(resource, 0)
        ):
            retained += 1
            continue
        now = utcnow()
        session.add(
            CanvasResourceDeletion(
                id=next_id(),
                created_at=now,
                updated_at=now,
                created_by=receipt.actor_user_id,
                updated_by=receipt.actor_user_id,
                user_id=upload.user_id,
                resource_id=resource.id,
                upload_id=upload.id,
                idempotency_hash=upload.idempotency_hash,
                request_hash=upload.request_hash,
                storage_locator=upload.storage_locator,
                chunk_count=(upload.byte_size + CHUNK_SIZE - 1) // CHUNK_SIZE
                if upload.mode == "chunked"
                else 0,
                status="pending",
                attempts=0,
                next_attempt_at=now,
                completed_at=None,
                error_code=None,
            )
        )
        session.delete(upload)
        session.flush()
        session.delete(resource)
        session.flush()
        retired += 1
    return retired, retained


def _retire_creations(
    session: Session,
    canvas: ProjectCanvas,
    receipt: CanvasWriteReceipt,
    preparations: list[CanvasCreationResource],
    settings: Settings,
) -> tuple[int, int]:
    deletions = CanvasResourceDeletionDAO(session)
    retired = retained = 0
    buckets = {
        settings.minio_image_bucket,
        settings.minio_video_bucket,
        settings.minio_audio_bucket,
    }
    for prepared in preparations:
        # 正式上传保持原墓碑协议；这里只接管早期已完成创建的真实准备出处。
        if deletions.upload(prepared.target_resource_id) is not None:
            continue
        snapshot = prepared.snapshot_json
        kind = snapshot.get("kind")
        if kind not in {"image", "video", "audio", "file"}:
            raise ValueError("Creation media cleanup has no frozen resource kind")
        model = CanvasBinaryResource if kind == "file" else MediaFile
        resource = session.scalar(
            select(model).where(model.id == prepared.target_resource_id).with_for_update()
        )
        if resource is None:
            continue
        if (
            resource.created_by != receipt.actor_user_id
            or resource.project_id != canvas.project_id
            or resource.scope_user_id is not None
        ):
            retained += 1
            continue
        fields = {
            "storage_locator": prepared.storage_locator,
            "original_name": snapshot.get("original_name"),
            "byte_size": snapshot.get("byte_size"),
            "checksum_sha256": snapshot.get("checksum_sha256"),
            "mime_type" if kind == "file" else "format_code": snapshot.get("mime_type"),
        }
        if kind != "file":
            fields.update({key: snapshot.get(key) for key in ("width", "height", "duration_ms")})
        if any(getattr(resource, key) != value for key, value in fields.items()):
            raise ValueError("Creation media no longer matches its frozen copy metadata")
        location = ObjectLocation.parse(prepared.storage_locator, buckets)
        source = ObjectLocation.parse(snapshot["storage_locator"], buckets)
        if (
            location.object_name != f"canvas/resources/{resource.id}"
            or location.bucket != source.bucket
        ):
            raise ValueError("Creation media is outside its reserved copy namespace")
        if (
            deletions.referenced(resource)
            or deletions.referenced(resource, history=True)
            or deletions.shared_by_library(resource, 0)
        ):
            retained += 1
            continue
        now = utcnow()
        session.add(
            CanvasResourceDeletion(
                id=next_id(),
                created_at=now,
                updated_at=now,
                created_by=receipt.actor_user_id,
                updated_by=receipt.actor_user_id,
                user_id=receipt.actor_user_id,
                resource_id=resource.id,
                upload_id=None,
                idempotency_hash=None,
                request_hash=None,
                creation_resource_id=prepared.id,
                disposal_receipt_id=receipt.id,
                storage_locator=prepared.storage_locator,
                chunk_count=0,
                status="pending",
                attempts=0,
                next_attempt_at=now,
                completed_at=None,
                error_code=None,
            )
        )
        session.flush()
        session.delete(resource)
        session.flush()
        retired += 1
    return retired, retained


def cleanup_canvas_recycle(
    factory: sessionmaker[Session], settings: Settings, *, apply: bool = False
) -> dict[str, int | bool]:
    result = {
        "examined": 0,
        "completed": 0,
        "deferred": 0,
        "retired_uploads": 0,
        "retained_uploads": 0,
        "retired_creation_resources": 0,
        "retained_creation_resources": 0,
        "failed": 0,
        "applied": apply,
    }
    cursor = 0
    while True:
        with factory() as session:
            candidates = CanvasRecycleCleanupDAO(session).candidates(cursor)
        if not candidates:
            return result
        for receipt_id, project_id, canvas_id in candidates:
            cursor = receipt_id
            try:
                with factory.begin() as session:
                    dao = CanvasRecycleCleanupDAO(session)
                    target = dao.lock_target(receipt_id, project_id, canvas_id)
                    if target is None:
                        continue
                    canvas, receipt = target
                    result["examined"] += 1
                    if dao.executing(canvas_id):
                        result["deferred"] += 1
                        continue
                    if not apply:
                        continue
                    uploads = dao.uploads(canvas_id, receipt.actor_user_id)
                    creation = CanvasCreationDAO(session)
                    preparations = creation.disposed_resources(canvas, receipt)
                    creation.clear_disposed(canvas, receipt)
                    dao.remove_payload(canvas_id)
                    CanvasRecycleExecutionDAO(session).clear(canvas, receipt)
                    # 项目关闭autoflush；引用检查必须看到本事务已释放的请求与工具引用。
                    session.flush()
                    retired, retained = _retire_uploads(session, canvas, receipt, uploads, settings)
                    legacy_retired, legacy_retained = _retire_creations(
                        session, canvas, receipt, preparations, settings
                    )
                    canvas.properties_json = dao.marker(receipt.id)
                    session.flush()
                # 只有事务提交成功才报告完成；字节尚由独立outbox处理。
                result["completed"] += 1
                result["retired_uploads"] += retired
                result["retained_uploads"] += retained
                result["retired_creation_resources"] += legacy_retired
                result["retained_creation_resources"] += legacy_retained
            except Exception as error:
                result["failed"] += 1
                logger.warning(
                    "Canvas recycle cleanup deferred: receipt=%s canvas=%s error=%s",
                    receipt_id,
                    canvas_id,
                    type(error).__name__,
                )
