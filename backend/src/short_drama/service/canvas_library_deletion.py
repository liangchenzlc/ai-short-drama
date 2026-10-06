"""Retire owned canvas uploads atomically; physical deletion is an outbox operation."""

from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_resource_deletion_dao import CanvasResourceDeletionDAO
from short_drama.domain import CanvasResourceDeletion

from .base import utcnow
from .canvas_document import media_references
from .canvas_resource_service import CHUNK_SIZE


def delete_library_asset(service, key: str, expected_status: str = "") -> None:
    expected_status = expected_status.strip()
    if expected_status not in {"", "archived"}:
        raise WorkflowError("canvas_asset_invalid", "删除条件无效", 422)
    with service._transaction():
        row = service.library.asset(key)
        if row is None:
            raise NotFound("Canvas library asset does not exist")
        if row.project_id:
            service.canvases.require_project(row.project_id, lock=True)
        row = service.library.asset(key, lock=True)
        if expected_status and row.status != expected_status:
            raise WorkflowError("canvas_asset_trash_conflict", "素材已不在回收站，未删除", 409)
        identifiers = {identifier for _, identifier in media_references(row.payload_json)}
        identifiers.update(
            ref.media_id or ref.binary_id for ref in service.library.references(row.id)
        )
        resources = service.resources.resource_map(identifiers)
        deletions = CanvasResourceDeletionDAO(service.session)
        retired = []
        for identifier in sorted(resources):
            resource = resources[identifier]
            if resource.created_by != service.canvases.actor_id:
                continue
            # Resources imported from the standard workspace keep that workspace's
            # lifecycle. Only a durable canvas upload authorizes this reclaimer.
            upload = deletions.upload(identifier)
            if upload is None:
                continue
            resource = service.session.scalar(
                select(type(resource)).where(type(resource).id == identifier).with_for_update()
            )
            if resource is None:
                continue
            if upload.status != "ready" or upload.user_id != service.canvases.actor_id:
                raise WorkflowError("canvas_asset_in_use", "素材资源状态已变化，请刷新后重试", 409)
            if upload.storage_locator != resource.storage_locator:
                raise WorkflowError("canvas_asset_in_use", "素材存储位置已变化，请核对后重试", 409)
            if deletions.referenced(resource):
                raise WorkflowError(
                    "canvas_asset_in_use",
                    "素材仍被画布、任务或业务记录引用，请先解除引用后再删除",
                    409,
                )
            if deletions.shared_by_library(resource, row.id):
                continue
            if deletions.referenced(resource, history=True):
                raise WorkflowError(
                    "canvas_asset_history_referenced", "素材仍被画布历史版本引用，不能彻底删除", 409
                )
            service.session.add(
                CanvasResourceDeletion(
                    **service.canvases.audit(),
                    user_id=service.canvases.actor_id,
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
                    next_attempt_at=utcnow(),
                    completed_at=None,
                    error_code=None,
                )
            )
            retired.append((upload, resource))
        # The receipt, asset removal and final resource removal commit together.
        # FK constraints are a second guard against references admitted concurrently.
        service.session.delete(row)
        for upload, _ in retired:
            service.session.delete(upload)
        service.session.flush()
        for _, resource in retired:
            service.session.delete(resource)
        service.session.flush()
