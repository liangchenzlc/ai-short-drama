"""项目资源副本：短数据库事务、稳定对象身份、来源保护与复制后的独立生命周期。"""

import hashlib
import logging
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta

from sqlalchemy import text

from short_drama.core.exceptions import Conflict, NotFound, StorageUnavailable, WorkflowError
from short_drama.dao.canvas_resource_deletion_dao import CanvasResourceDeletionDAO
from short_drama.db.access import require_project
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasResourceCopySource,
    CanvasResourceUpload,
    MediaFile,
)
from short_drama.schemas.canvas_resource import CanvasResourceCopyRequest
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id

from .base import utcnow
from .canvas_document import content_hash
from .canvas_resource_service import CanvasResourceService

logger = logging.getLogger(__name__)


@contextmanager
def canvas_copy_lock(engine, identifier: int):
    """Serializes I/O and expiry cleanup without keeping a row transaction open."""
    with engine.connect() as connection:
        name = connection.scalar(
            text(
                "SELECT CONCAT('canvas:copy:', LEFT(SHA2(CONCAT(DATABASE(), ':', :id), 256), 48))"
            ),
            {"id": str(identifier)},
        )
        if connection.scalar(text("SELECT GET_LOCK(:name, 5)"), {"name": name}) != 1:
            raise Conflict("Resource copy is busy; retry the same request")
        try:
            yield
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})


def release_copy_source(source: CanvasResourceCopySource) -> None:
    source.source_media_id = source.source_binary_id = None
    source.released_at = utcnow()


class CanvasResourceCopyService(CanvasResourceService):
    def copy(self, payload: CanvasResourceCopyRequest, key: str):
        request = CanvasResourceCopyRequest.model_validate(payload.model_dump())
        if not key or not key.strip() or len(key) > 512:
            raise WorkflowError("canvas_idempotency_required", "资源复制需要稳定请求标识", 422)
        identity = hashlib.sha256(key.strip().encode()).hexdigest()
        digest = content_hash(
            {"operation": "canvas.resource.copy", **request.model_dump(mode="json")}
        )
        identifier = self._reserve(request, identity, digest)
        with canvas_copy_lock(self.session.get_bind().engine, identifier):
            with self._global_order_lock(), self._transaction():
                upload, source, snapshot = self._locked_copy(identifier)
                if upload.status == "ready":
                    return self._ready(upload)
                target_locator = upload.storage_locator
            # A durable FK pins the source. No project/resource row transaction
            # spans the object-store request; cleanup takes the same copy mutex.
            location = self.location(snapshot["storage_locator"])
            target = self.location(target_locator)
            try:
                stored = self.storage.copy(
                    location.bucket, location.object_name, target.object_name
                )
            except (StorageUnavailable, NotFound) as error:
                logger.warning(
                    "Canvas resource copy deferred: upload=%s error=%s",
                    identifier,
                    type(error).__name__,
                )
                raise WorkflowError(
                    "canvas_resource_copy_failed", "资源复制未完成，请保留原请求重试", 503
                ) from None
            if stored.storage_locator != target_locator or stored.size != snapshot["byte_size"]:
                raise WorkflowError("canvas_resource_copy_changed", "复制文件大小或位置不符", 409)
            with self._global_order_lock(), self._transaction():
                upload, source, current = self._locked_copy(identifier)
                if upload.status == "ready":
                    return self._ready(upload)
                if current != snapshot:
                    raise WorkflowError("canvas_resource_copy_changed", "复制来源已变化", 409)
                binary = snapshot["kind"] == "file"
                values = {
                    **self.canvases.audit(),
                    "id": upload.reserved_resource_id,
                    "project_id": upload.project_id,
                    "scope_user_id": None,
                    "published_at": None,
                    "storage_locator": upload.storage_locator,
                    "original_name": snapshot["original_name"],
                    "byte_size": snapshot["byte_size"],
                    "checksum_sha256": snapshot["checksum_sha256"],
                }
                if binary:
                    resource = CanvasBinaryResource(
                        **values, resource_kind="file", mime_type=snapshot["mime_type"]
                    )
                else:
                    resource = MediaFile(
                        **values,
                        format_code=snapshot["mime_type"],
                        width=snapshot["width"],
                        height=snapshot["height"],
                        duration_ms=snapshot["duration_ms"],
                        # Provider preview manifests may point into the original
                        # scope. Only the copied file's intrinsic metadata travels.
                        video_metadata=None,
                    )
                self.session.add(resource)
                self.session.flush()
                setattr(upload, "binary_id" if binary else "media_id", resource.id)
                upload.status, upload.updated_at = "ready", utcnow()
                release_copy_source(source)
                self.session.flush()
                return self.read_model(resource)

    def _lock_scopes(self, canvas_key, source_id=None):
        canvas = self.resources.canvas(canvas_key)
        resource = self.resources.resource(source_id) if source_id else None
        if canvas is None or source_id and resource is None:
            raise NotFound("Canvas or copy source does not exist")
        projects = {canvas.project_id}
        if resource is not None and resource.project_id:
            projects.add(resource.project_id)
        # Opposite-direction imports take both project locks in the same order.
        for project_id in sorted(projects):
            require_project(self.session, project_id, lock=True)
        canvas = self.canvases.require_canvas(canvas.project_id, canvas.id, lock=True)
        if source_id:
            resource = self.resources.resource(source_id, lock=True)
            if resource is None:
                raise NotFound("Copy source does not exist")
        return canvas, resource

    def _reserve(self, request, identity, digest):
        with self._global_order_lock(), self._transaction():
            old = self.resources.by_key(self.actor_id, identity, lock=False)
            deletion = CanvasResourceDeletionDAO(self.session).receipt(
                user_id=self.actor_id, identity=identity
            )
            # An acknowledged independent copy remains readable after losing
            # access to the original; an unfinished copy rechecks both scopes.
            source_id = (
                None
                if deletion is not None or old is not None and old.status == "ready"
                else request.source_resource_id
            )
            canvas, resource = self._lock_scopes(request.canvas_key, source_id)
            if deletion is not None:
                self._same_request(deletion.request_hash, digest)
                raise WorkflowError(
                    "canvas_resource_deleted", "此资源副本已彻底删除，请明确重新复制", 410
                )
            if old is not None:
                self._same_request(old.request_hash, digest)
                return old.id
            if self.resources.pending_count(self.actor_id, utcnow()) >= 32:
                raise WorkflowError("canvas_upload_busy", "待完成上传或复制过多，请稍后重试", 429)
            snapshot = self._snapshot_resource(resource)
            snapshot["resource_ancestors"] = [
                str(value)
                for value in sorted(
                    self.resources.copy_ancestors({resource.id}).get(resource.id, set())
                )
            ]
            location = self.location(resource.storage_locator)
            reserved = next_id()
            upload = CanvasResourceUpload(
                **self.canvases.audit(),
                scope_user_id=None,
                project_id=canvas.project_id,
                canvas_id=canvas.id,
                user_id=self.actor_id,
                idempotency_hash=identity,
                request_hash=digest,
                mode="copy",
                status="pending",
                declared_json=request.model_dump(mode="json"),
                reserved_resource_id=reserved,
                storage_locator=ObjectLocation(
                    location.bucket, f"canvas/resources/{reserved}"
                ).locator,
                byte_size=resource.byte_size,
                expires_at=utcnow() + timedelta(minutes=90),
                media_id=None,
                binary_id=None,
            )
            self.session.add(upload)
            self.session.flush()
            binary = isinstance(resource, CanvasBinaryResource)
            self.session.add(
                CanvasResourceCopySource(
                    id=next_id(),
                    upload_id=upload.id,
                    original_resource_id=resource.id,
                    source_media_id=None if binary else resource.id,
                    source_binary_id=resource.id if binary else None,
                    snapshot_json=deepcopy(snapshot),
                    released_at=None,
                    created_at=utcnow(),
                )
            )
            self.session.flush()
            return upload.id

    @staticmethod
    def _same_request(actual, expected):
        if actual != expected:
            raise WorkflowError("canvas_upload_conflict", "此请求标识已用于其他上传或复制", 409)

    @staticmethod
    def _snapshot_resource(resource):
        binary = isinstance(resource, CanvasBinaryResource)
        mime = resource.mime_type if binary else resource.format_code
        kind = "file" if binary else mime.partition("/")[0]
        if kind not in {"image", "video", "audio", "file"} or not resource.byte_size:
            raise WorkflowError("canvas_resource_invalid", "复制来源缺少实际文件类型或大小", 422)
        return {
            "kind": kind,
            "mime_type": mime,
            "storage_locator": resource.storage_locator,
            "original_name": resource.original_name,
            "byte_size": resource.byte_size,
            "checksum_sha256": resource.checksum_sha256,
            "width": getattr(resource, "width", None),
            "height": getattr(resource, "height", None),
            "duration_ms": getattr(resource, "duration_ms", None),
        }

    def _locked_copy(self, identifier):
        upload = self.resources.upload(identifier, lock=False)
        if upload is None or upload.mode != "copy":
            raise NotFound("Resource copy does not exist")
        request = CanvasResourceCopyRequest.model_validate(upload.declared_json)
        canvas, resource = self._lock_scopes(
            request.canvas_key,
            None if upload.status == "ready" else request.source_resource_id,
        )
        upload = self.resources.upload(identifier)
        source = self.resources.copy_source(identifier)
        if upload is None or source is None or upload.canvas_id != canvas.id:
            raise NotFound("Resource copy does not exist")
        if upload.status == "ready":
            return upload, source, None
        snapshot = self._snapshot_resource(resource)
        if "resource_ancestors" in source.snapshot_json:
            snapshot["resource_ancestors"] = deepcopy(source.snapshot_json["resource_ancestors"])
        if snapshot != source.snapshot_json:
            raise WorkflowError("canvas_resource_copy_changed", "来源已变化，请明确重新复制", 409)
        source.source_media_id = None if snapshot["kind"] == "file" else resource.id
        source.source_binary_id = resource.id if snapshot["kind"] == "file" else None
        source.released_at = None
        upload.expires_at, upload.updated_at = utcnow() + timedelta(minutes=90), utcnow()
        self.session.flush()
        return upload, source, snapshot
