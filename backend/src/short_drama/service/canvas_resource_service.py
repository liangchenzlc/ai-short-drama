"""Uploads are private until the source editor attaches a resource to shared work."""

import hashlib
import logging
from datetime import timedelta
from tempfile import SpooledTemporaryFile
from typing import BinaryIO
from uuid import uuid4

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.dao.canvas_resource_deletion_dao import CanvasResourceDeletionDAO
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasResourceChunk,
    CanvasResourceUpload,
    MediaFile,
)
from short_drama.schemas.canvas_resource import CanvasResourceRead, CanvasResourceStart
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id

from .base import BaseService, utcnow
from .canvas_document import content_hash
from .canvas_resource_io import inspect_resource
from .canvas_service import CanvasService, iso

CHUNK_SIZE = 8 * 1024**2
MULTIPART_LIMIT = 50 * 1024**2
logger = logging.getLogger(__name__)


class CanvasResourceService(BaseService):
    model = CanvasResourceUpload

    def __init__(self, session, settings, storage):
        super().__init__(session)
        self.settings, self.storage = settings, storage
        self.resources = CanvasResourceDAO(session)
        self.canvases = CanvasService(session)

    @property
    def actor_id(self):
        return self.canvases.actor_id

    def location(self, locator):
        return ObjectLocation.parse(
            locator,
            {
                self.settings.minio_image_bucket,
                self.settings.minio_video_bucket,
                self.settings.minio_audio_bucket,
            },
        )

    def _scope(self, canvas_key):
        if not canvas_key:
            return self.actor_id, None, None
        canvas = self.resources.canvas(canvas_key)
        if canvas is None:
            raise NotFound("Canvas does not exist")
        self.canvases.require_canvas(canvas.project_id, canvas.id, lock=True)
        return None, canvas.project_id, canvas.id

    def _prepare(self, request, key, canvas_key, mode, checksum=None):
        identity = hashlib.sha256(
            (key.strip() if key and key.strip() else uuid4().hex).encode()
        ).hexdigest()
        declared = request.model_dump(exclude={"idempotency_key"})
        digest = content_hash(
            {"request": declared, "canvas_key": canvas_key, "mode": mode, "sha256": checksum}
        )
        # Reserve keys and enforce the per-user quota without locking the audit
        # parent row needed by a concurrent project-locked canvas autosave.
        with self._global_order_lock(), self._transaction():
            scope_user_id, project_id, canvas_id = self._scope(canvas_key)
            deletion = CanvasResourceDeletionDAO(self.session).receipt(
                user_id=self.actor_id, identity=identity
            )
            if deletion is not None:
                if deletion.request_hash != digest:
                    raise WorkflowError("canvas_upload_conflict", "此上传标识已用于其他文件", 409)
                raise WorkflowError(
                    "canvas_resource_deleted", "此上传资源已彻底删除，请明确重新上传", 410
                )
            upload = self.resources.by_key(self.actor_id, identity)
            if upload is not None:
                if upload.request_hash != digest:
                    raise WorkflowError(
                        "canvas_upload_conflict", "此上传标识已用于其他文件，请保留原文件重试", 409
                    )
                if upload.status == "pending":
                    upload.expires_at = utcnow() + timedelta(minutes=90)
                return upload.id
            if self.resources.pending_count(self.actor_id, utcnow()) >= 32:
                raise WorkflowError("canvas_upload_busy", "待完成上传过多，请稍后重试", 429)
            reserved = next_id()
            bucket = {
                "image": self.settings.minio_image_bucket,
                "video": self.settings.minio_video_bucket,
                "audio": self.settings.minio_audio_bucket,
                "file": self.settings.minio_video_bucket,
            }[request.kind]
            locator = ObjectLocation(bucket, f"canvas/resources/{reserved}").locator
            upload = CanvasResourceUpload(
                **self.canvases.audit(),
                scope_user_id=scope_user_id,
                project_id=project_id,
                canvas_id=canvas_id,
                user_id=self.actor_id,
                idempotency_hash=identity,
                request_hash=digest,
                mode=mode,
                status="pending",
                declared_json=declared,
                reserved_resource_id=reserved,
                storage_locator=locator,
                byte_size=request.size,
                expires_at=utcnow() + timedelta(minutes=90),
                media_id=None,
                binary_id=None,
            )
            self.session.add(upload)
            self.session.flush()
            return upload.id

    def _upload(self, identifier, *, mode=None):
        upload = self.resources.upload(identifier)
        if (
            upload is None
            and CanvasResourceDeletionDAO(self.session).receipt(
                user_id=self.actor_id, upload_id=identifier
            )
            is not None
        ):
            raise WorkflowError(
                "canvas_resource_deleted", "此上传资源已彻底删除，请明确重新上传", 410
            )
        if upload is None or (mode and upload.mode != mode):
            raise NotFound("Upload does not exist")
        if upload.canvas_id:
            self.canvases.require_canvas(upload.project_id, upload.canvas_id)
        if upload.status != "ready" and upload.expires_at < utcnow():
            raise WorkflowError(
                "canvas_upload_expired", "上传会话已过期，请用原上传标识重新开始", 410
            )
        return upload

    def start(self, request: CanvasResourceStart, key: str | None, canvas_key: str | None):
        identifier = self._prepare(request, key or request.idempotency_key, canvas_key, "chunked")
        return {
            "upload_id": identifier,
            "chunk_size": CHUNK_SIZE,
            "chunk_count": (request.size + CHUNK_SIZE - 1) // CHUNK_SIZE,
        }

    def upload(
        self, stream: BinaryIO, request: CanvasResourceStart, declared: str, key, canvas_key
    ):
        if request.size > MULTIPART_LIMIT:
            raise WorkflowError("canvas_upload_too_large", "文件超过 50 MiB，请使用分片上传", 413)
        inspected = inspect_resource(
            stream, request.file_name, request.kind, declared, self.settings
        )
        if inspected.size != request.size:
            raise WorkflowError("canvas_upload_incomplete", "上传文件长度不一致", 422)
        identifier = self._prepare(request, key, canvas_key, "multipart", inspected.checksum)
        with self._transaction():
            upload = self._upload(identifier, mode="multipart")
            return self._complete(upload, stream, inspected)

    def put_chunk(self, identifier: int, index: int, body: bytes):
        with self._transaction():
            upload = self._upload(identifier, mode="chunked")
            expected = min(CHUNK_SIZE, upload.byte_size - index * CHUNK_SIZE)
            if index < 0 or expected <= 0:
                raise WorkflowError("canvas_upload_chunk_invalid", "分片序号无效", 422)
            if len(body) != expected:
                raise WorkflowError("canvas_upload_incomplete", "分片大小与预期不符", 422)
            digest = hashlib.sha256(body).hexdigest()
            chunks = self.resources.chunks(identifier)
            previous = next((chunk for chunk in chunks if chunk.chunk_index == index), None)
            if previous:
                if previous.checksum_sha256 != digest:
                    raise WorkflowError(
                        "canvas_upload_conflict", "此分片已上传不同内容，请保留原文件重试", 409
                    )
                return {"index": index}
            if upload.status == "ready":
                raise WorkflowError("canvas_upload_conflict", "已完成上传不能修改分片", 409)
            target = self.location(upload.storage_locator)
            chunk_key = f"canvas/uploads/{upload.id}/{index}"
            from io import BytesIO

            self.storage.put(
                target.bucket, chunk_key, BytesIO(body), len(body), "application/octet-stream"
            )
            self.session.add(
                CanvasResourceChunk(
                    id=next_id(),
                    upload_id=identifier,
                    chunk_index=index,
                    storage_locator=ObjectLocation(target.bucket, chunk_key).locator,
                    byte_size=len(body),
                    checksum_sha256=digest,
                    created_at=utcnow(),
                )
            )
            upload.expires_at = utcnow() + timedelta(minutes=90)
            upload.updated_at = utcnow()
            self.session.flush()
            return {"index": index}

    def complete(self, identifier: int):
        result = self._complete_chunks(identifier)
        # Committed metadata is authoritative. Cleanup failure must not report a
        # successful upload as failed; the explicit cleanup job retries these keys.
        try:
            with self._transaction():
                upload = self._upload(identifier, mode="chunked")
                if upload.status == "ready":
                    for part in self.resources.chunks(identifier):
                        location = self.location(part.storage_locator)
                        self.storage.remove(location.bucket, location.object_name)
        except Exception as error:
            logger.warning(
                "Canvas upload chunk cleanup deferred: upload=%s error=%s",
                identifier,
                type(error).__name__,
            )
        return result

    def _complete_chunks(self, identifier: int):
        with self._transaction():
            upload = self._upload(identifier, mode="chunked")
            if upload.status == "ready":
                return self._ready(upload)
            chunks = self.resources.chunks(identifier)
            count = (upload.byte_size + CHUNK_SIZE - 1) // CHUNK_SIZE
            if len(chunks) != count or any(part.chunk_index != i for i, part in enumerate(chunks)):
                raise WorkflowError("canvas_upload_incomplete", "分片尚未全部上传，请重试", 409)
            with SpooledTemporaryFile(max_size=CHUNK_SIZE, mode="w+b") as stream:
                for part in chunks:
                    location = self.location(part.storage_locator)
                    digest, written = hashlib.sha256(), 0
                    with self.storage.open(location.bucket, location.object_name) as response:
                        for data in response.stream(1024 * 1024):
                            written += len(data)
                            if written > part.byte_size:
                                raise WorkflowError(
                                    "canvas_upload_incomplete", "存储分片长度不符", 409
                                )
                            digest.update(data)
                            stream.write(data)
                    if written != part.byte_size or digest.hexdigest() != part.checksum_sha256:
                        raise WorkflowError(
                            "canvas_upload_incomplete", "存储分片校验失败，请重试", 409
                        )
                request = CanvasResourceStart.model_validate(upload.declared_json)
                inspected = inspect_resource(
                    stream, request.file_name, request.kind, "", self.settings
                )
                if inspected.size != upload.byte_size:
                    raise WorkflowError("canvas_upload_incomplete", "合并文件大小不符", 409)
                return self._complete(upload, stream, inspected)

    def _complete(self, upload, stream, inspected):
        if upload.status == "ready":
            return self._ready(upload)
        location = self.location(upload.storage_locator)
        stream.seek(0)
        self.storage.put(
            location.bucket, location.object_name, stream, inspected.size, inspected.mime_type
        )
        values = {
            **self.canvases.audit(),
            "id": upload.reserved_resource_id,
            "project_id": upload.project_id,
            "scope_user_id": upload.scope_user_id,
            "published_at": None,
            "storage_locator": upload.storage_locator,
            "original_name": upload.declared_json["file_name"],
            "byte_size": inspected.size,
            "checksum_sha256": inspected.checksum,
        }
        if upload.declared_json["kind"] == "file":
            resource = CanvasBinaryResource(
                **values, resource_kind="file", mime_type=inspected.mime_type
            )
            field = "binary_id"
        else:
            resource = MediaFile(
                **values,
                format_code=inspected.mime_type,
                width=inspected.width,
                height=inspected.height,
                duration_ms=inspected.duration_ms,
                video_metadata=inspected.video_metadata,
            )
            field = "media_id"
        self.session.add(resource)
        self.session.flush()
        upload.status, upload.updated_at = "ready", utcnow()
        setattr(upload, field, resource.id)
        self.session.flush()
        return self.read_model(resource)

    def _ready(self, upload):
        resource = self.resources.resource(upload.media_id or upload.binary_id)
        if resource is None:
            raise NotFound("Resource does not exist")
        return self.read_model(resource)

    def read_model(self, resource):
        mime = (
            resource.mime_type
            if isinstance(resource, CanvasBinaryResource)
            else resource.format_code
        )
        return CanvasResourceRead(
            id=resource.id,
            user_id=resource.created_by,
            kind="file" if isinstance(resource, CanvasBinaryResource) else mime.partition("/")[0],
            mime_type=mime,
            size=resource.byte_size or 0,
            width=getattr(resource, "width", None) or 0,
            height=getattr(resource, "height", None) or 0,
            duration_ms=getattr(resource, "duration_ms", None) or 0,
            etag=resource.checksum_sha256 or "",
            created_at=iso(resource.created_at),
            updated_at=iso(resource.updated_at),
        )

    def read(self, identifier: int):
        with self._transaction(read_only=True):
            resource = self.resources.resource(identifier)
            if resource is None:
                raise NotFound("Resource does not exist")
            return self.read_model(resource), resource.storage_locator
