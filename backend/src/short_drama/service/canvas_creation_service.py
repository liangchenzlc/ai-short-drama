"""Prepare independent files before atomically publishing a complete first canvas."""

import hashlib
import logging
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from short_drama.core.exceptions import NotFound, StorageUnavailable, WorkflowError
from short_drama.dao.canvas_creation_dao import CanvasCreationDAO
from short_drama.db.access import require_project
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasCreationAttempt,
    CanvasCreationResource,
    CanvasDrawing,
    CanvasResourceCopySource,
    CanvasResourceUpload,
    MediaFile,
)
from short_drama.schemas.canvas import CanvasDocument
from short_drama.schemas.canvas_creation import CanvasCreationRequest as CanvasCreateRequest
from short_drama.schemas.canvas_creation import canvas_creation_payload
from short_drama.schemas.canvas_resource import CanvasResourceCopyRequest
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id

from .base import utcnow
from .canvas_document import content_hash, media_references, remap_resource_references
from .canvas_drawing_document import drawing_resource_ids, remap_drawing_resources
from .canvas_drawing_service import CanvasDrawingService
from .canvas_folder_state import canvas_folder_write_lock
from .canvas_media_locators import canonicalize_canvas_media
from .canvas_resource_copy import CanvasResourceCopyService, canvas_copy_lock

logger = logging.getLogger(__name__)


class CanvasCreationService(CanvasResourceCopyService):
    def __init__(self, session, settings, storage):
        super().__init__(session, settings, storage)
        self.creations = CanvasCreationDAO(session)
        self.drawings = CanvasDrawingService(session)

    def create(self, payload: CanvasCreateRequest, key: str, *, project_id: int | None = None):
        request = CanvasCreateRequest.model_validate(payload.model_dump(exclude_unset=True))
        drawing_documents = self._drawing_documents(request)
        reserved = self._reserve_creation(request, key, project_id)
        if isinstance(reserved, dict):
            return reserved
        with canvas_copy_lock(self.session.get_bind().engine, reserved):
            with self._transaction(read_only=True):
                identifiers = [row.id for row in self.creations.resources(reserved, lock=False)]
            for identifier in identifiers:
                self._copy_prepared_file(reserved, identifier)
            with (
                self._global_order_lock(),
                canvas_folder_write_lock(self.session),
                self._transaction(),
            ):
                attempt = self._lock_creation(reserved)
                if attempt.status == "ready":
                    return self._replay_creation(attempt)
                files = self.creations.resources(reserved)
                if any(row.status != "copied" for row in files):
                    raise WorkflowError("canvas_creation_pending", "画布媒体尚未准备完成", 409)

                def prepare(canvas, document):
                    mapping = {row.source_resource_id: row.target_resource_id for row in files}
                    for row in files:
                        self._attach_prepared_file(canvas, row)
                    for identifier in self._creation_resource_ids(request, drawing_documents):
                        mapping.setdefault(identifier, identifier)
                    self._attach_drawings(canvas, drawing_documents, mapping)
                    if document is not None:
                        raw = document.model_dump(mode="json", by_alias=True, exclude_unset=True)
                        document = CanvasDocument.model_validate(
                            remap_resource_references(raw, mapping)
                        )
                        for _, identifier in media_references(raw):
                            mapping.setdefault(identifier, identifier)
                    aliases = self.resources.copy_ancestors(set(mapping.values()))
                    return document, {
                        "resource_map": {str(k): str(v) for k, v in mapping.items()},
                        "resource_aliases": {
                            str(k): [str(v) for v in sorted(values)]
                            for k, values in aliases.items()
                        },
                    }

                result = self.canvases.create_in_transaction(
                    request,
                    key,
                    project_id=project_id,
                    source_key=attempt.source_key,
                    prepare_document=prepare,
                )
                attempt.status = "ready"
                attempt.result_json = deepcopy(result)
                attempt.updated_at = utcnow()
                self.session.flush()
                return result

    def _reserve_creation(self, request, key, project_id):
        operation = "canvas.create" if project_id is not None else "canvas.workspace.create"
        original = canvas_creation_payload(request)
        payload = original if project_id is None else {"project_id": str(project_id), **original}
        with self._global_order_lock(), self._transaction():
            if project_id is not None:
                self.canvases.require_project(project_id, lock=True)
            digest, receipt = self.canvases.begin_write(key, operation, payload)
            if receipt:
                return deepcopy(receipt.result_json)
            old = self.creations.by_key(self.actor_id, key)
            if old is not None:
                if old.operation_kind != operation or old.request_hash != digest:
                    raise WorkflowError(
                        "canvas_idempotency_conflict", "此请求标识已用于另一份画布", 409
                    )
                if old.status == "ready":
                    return self._replay_creation(old)
                return old.id
            if self.creations.pending_count(self.actor_id, utcnow()) >= 32:
                raise WorkflowError("canvas_creation_busy", "待完成画布过多，请先重试保存", 429)
            document = request.source_document
            source_key = request.source_key or (document.id if document else uuid4().hex)
            if document and (
                document.id != source_key
                or document.project_id
                or document.workspace_project_id
                not in {None, document.id if project_id is None else str(project_id)}
            ):
                raise WorkflowError("canvas_identity_mismatch", "画布所属项目不一致", 422)
            raw = document.model_dump(mode="json", by_alias=True) if document else {}
            canonicalize_canvas_media(raw)
            identifiers = self._creation_resource_ids(request, self._drawing_documents(request))
            sources = self.resources.resource_map(identifiers)
            if set(sources) != identifiers:
                raise NotFound("Canvas creation source does not exist")
            # Source projects can be standard projects. Lock them before their files.
            for owner in sorted({row.project_id for row in sources.values() if row.project_id}):
                require_project(self.session, owner, lock=True)
            attempt = CanvasCreationAttempt(
                **self.canvases.audit(),
                user_id=self.actor_id,
                idempotency_key=key,
                operation_kind=operation,
                request_hash=digest,
                request_json=deepcopy(original),
                source_key=source_key,
                target_project_id=project_id,
                status="pending",
                expires_at=utcnow() + timedelta(minutes=90),
                result_json=None,
            )
            self.session.add(attempt)
            self.session.flush()
            ancestors = self.resources.copy_ancestors(identifiers)
            for identifier in sorted(identifiers):
                resource = self.resources.resource(identifier, lock=True)
                if resource is None:
                    raise NotFound("Canvas creation source does not exist")
                if project_id is not None and resource.project_id == project_id:
                    continue
                snapshot = self._snapshot_resource(resource)
                snapshot["resource_ancestors"] = [
                    str(value) for value in sorted(ancestors.get(identifier, set()))
                ]
                binary = isinstance(resource, CanvasBinaryResource)
                target = next_id()
                self.session.add(
                    CanvasCreationResource(
                        **self.canvases.audit(),
                        attempt_id=attempt.id,
                        user_id=self.actor_id,
                        source_resource_id=identifier,
                        source_media_id=None if binary else identifier,
                        source_binary_id=identifier if binary else None,
                        snapshot_json=snapshot,
                        target_resource_id=target,
                        storage_locator=ObjectLocation(
                            self.location(resource.storage_locator).bucket,
                            f"canvas/resources/{target}",
                        ).locator,
                        status="pending",
                        copied_at=None,
                        released_at=None,
                    )
                )
            self.session.flush()
            return attempt.id

    def _drawing_documents(self, request):
        return {
            drawing.drawing_id: self.drawings._document(drawing, None)
            for drawing in request.drawing_documents
        }

    @staticmethod
    def _creation_resource_ids(request, drawings):
        raw = (
            request.source_document.model_dump(mode="json", by_alias=True)
            if request.source_document
            else {}
        )
        identifiers = {identifier for _, identifier in media_references(raw)}
        for document in drawings.values():
            identifiers.update(drawing_resource_ids(document))
        return identifiers

    def _attach_drawings(self, canvas, documents, mapping):
        for key, source in documents.items():
            document = remap_drawing_resources(source, mapping)
            resources = self.drawings._resources(canvas, document)
            drawing = CanvasDrawing(
                **self.canvases.child(canvas), source_key=key, row_version=1, archived_at=None
            )
            self.session.add(drawing)
            self.session.flush()
            self.drawings.append_version(canvas, drawing, document, resources)

    def _lock_creation(self, identifier):
        attempt = self.creations.attempt(identifier, lock=False)
        if attempt is None:
            raise NotFound("Canvas creation does not exist")
        if attempt.target_project_id is not None:
            self.canvases.require_project(attempt.target_project_id, lock=True)
        return self.creations.attempt(identifier)

    def _replay_creation(self, attempt):
        result = attempt.result_json
        self.canvases.require_canvas(int(result["project_id"]), int(result["id"]), lock=True)
        return deepcopy(result)

    def _pending_file(self, attempt, identifier):
        row = next(item for item in self.creations.resources(attempt.id) if item.id == identifier)
        if row.status != "pending":
            return row
        source = self.resources.resource(row.source_resource_id)
        if source is None:
            raise NotFound("Canvas creation source does not exist")
        if source.project_id:
            require_project(self.session, source.project_id, lock=True)
        source = self.resources.resource(row.source_resource_id, lock=True)
        if source is None:
            raise NotFound("Canvas creation source does not exist")
        snapshot = self._snapshot_resource(source)
        snapshot["resource_ancestors"] = row.snapshot_json["resource_ancestors"]
        if snapshot != row.snapshot_json:
            raise WorkflowError("canvas_resource_copy_changed", "画布复制来源已变化", 409)
        binary = isinstance(source, CanvasBinaryResource)
        row.source_media_id = None if binary else source.id
        row.source_binary_id = source.id if binary else None
        row.released_at = None
        attempt.expires_at = utcnow() + timedelta(minutes=90)
        attempt.updated_at = utcnow()
        self.session.flush()
        return row

    def _copy_prepared_file(self, attempt_id, identifier):
        with self._global_order_lock(), self._transaction():
            attempt = self._lock_creation(attempt_id)
            if attempt.status == "ready":
                return
            row = self._pending_file(attempt, identifier)
            if row.status == "copied":
                return
            snapshot, target_locator = deepcopy(row.snapshot_json), row.storage_locator
        source, target = self.location(snapshot["storage_locator"]), self.location(target_locator)
        try:
            stored = self.storage.copy(source.bucket, source.object_name, target.object_name)
        except (StorageUnavailable, NotFound) as error:
            logger.warning(
                "Canvas creation copy deferred: attempt=%s file=%s error=%s",
                attempt_id,
                identifier,
                type(error).__name__,
            )
            raise WorkflowError(
                "canvas_resource_copy_failed", "画布媒体复制未完成，请保留原请求重试", 503
            ) from None
        if stored.storage_locator != target_locator or stored.size != snapshot["byte_size"]:
            raise WorkflowError("canvas_resource_copy_changed", "复制文件大小或位置不符", 409)
        with self._global_order_lock(), self._transaction():
            attempt = self._lock_creation(attempt_id)
            row = self._pending_file(attempt, identifier)
            row.status = "copied"
            row.source_media_id = row.source_binary_id = None
            row.copied_at = row.released_at = row.updated_at = utcnow()
            self.session.flush()

    def _attach_prepared_file(self, canvas, row):
        snapshot = row.snapshot_json
        binary = snapshot["kind"] == "file"
        values = {
            **self.canvases.audit(),
            "id": row.target_resource_id,
            "scope_user_id": None,
            "project_id": canvas.project_id,
            "published_at": None,
            "storage_locator": row.storage_locator,
            "original_name": snapshot["original_name"],
            "byte_size": snapshot["byte_size"],
            "checksum_sha256": snapshot["checksum_sha256"],
        }
        resource = (
            CanvasBinaryResource(**values, resource_kind="file", mime_type=snapshot["mime_type"])
            if binary
            else MediaFile(
                **values,
                format_code=snapshot["mime_type"],
                width=snapshot["width"],
                height=snapshot["height"],
                duration_ms=snapshot["duration_ms"],
                video_metadata=None,
            )
        )
        self.session.add(resource)
        self.session.flush()
        request = CanvasResourceCopyRequest(
            canvas_key=canvas.source_key, source_resource_id=row.source_resource_id
        ).model_dump(mode="json")
        identity = f"canvas-normalize:v1:{canvas.source_key}:{row.source_resource_id}"
        upload = CanvasResourceUpload(
            **self.canvases.audit(),
            scope_user_id=None,
            project_id=canvas.project_id,
            canvas_id=canvas.id,
            user_id=self.actor_id,
            idempotency_hash=hashlib.sha256(identity.encode()).hexdigest(),
            request_hash=content_hash({"operation": "canvas.resource.copy", **request}),
            mode="copy",
            status="ready",
            declared_json=request,
            reserved_resource_id=row.target_resource_id,
            storage_locator=row.storage_locator,
            byte_size=snapshot["byte_size"],
            expires_at=utcnow(),
            media_id=None if binary else resource.id,
            binary_id=resource.id if binary else None,
        )
        self.session.add(upload)
        self.session.flush()
        self.session.add(
            CanvasResourceCopySource(
                id=next_id(),
                upload_id=upload.id,
                original_resource_id=row.source_resource_id,
                source_media_id=None,
                source_binary_id=None,
                snapshot_json=deepcopy(snapshot),
                released_at=row.released_at,
                created_at=utcnow(),
            )
        )
        row.status, row.updated_at = "attached", utcnow()
        self.session.flush()
