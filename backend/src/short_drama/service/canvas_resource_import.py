"""外部素材通过有界下载进入既有上传流程；不把临时 URL 作为媒体身份。"""

import hashlib
import logging
import mimetypes
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from pydantic import Field
from sqlalchemy import text

from short_drama.ai.transport import SafeTransport, validated_url
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.canvas_resource_deletion_dao import CanvasResourceDeletionDAO
from short_drama.schemas.canvas_resource import CanvasResourceImport, CanvasResourceStart

from .base import utcnow
from .canvas_document import content_hash
from .canvas_resource_service import MULTIPART_LIMIT, CanvasResourceService

logger = logging.getLogger(__name__)


@contextmanager
def import_lock(engine, actor_id: int, identity: str):
    """同一作者/请求串行恢复；外部 I/O 不持有项目、上传行或用户全局锁。"""
    with engine.connect() as connection:
        name = connection.scalar(
            text(
                "SELECT CONCAT('canvas:import:', "
                "LEFT(SHA2(CONCAT(DATABASE(), ':', :actor, ':', :identity), 256), 48))"
            ),
            {"actor": str(actor_id), "identity": identity},
        )
        if connection.scalar(text("SELECT GET_LOCK(:name, 5)"), {"name": name}) != 1:
            raise Conflict("Resource import is busy; retry the same request")
        try:
            yield
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})


class ImportedResourceStart(CanvasResourceStart):
    """仅内部使用；来源 URL 只保留摘要，重传沿用原文件声明和 MIME。"""

    import_request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    import_mime_type: str = Field(min_length=1, max_length=127)


class CanvasResourceImportService(CanvasResourceService):
    def import_url(self, payload: CanvasResourceImport, key: str, canvas_key: str | None):
        request = CanvasResourceImport.model_validate(payload.model_dump())
        if not key or not key.strip() or len(key) > 512:
            raise WorkflowError("canvas_idempotency_required", "素材导入需要稳定请求标识", 422)
        identity = hashlib.sha256(key.strip().encode()).hexdigest()
        with import_lock(self.session.get_bind().engine, self.actor_id, identity):
            return self._import(request, key, canvas_key, identity)

    def _import(
        self, request: CanvasResourceImport, key: str, canvas_key: str | None, identity: str
    ):
        try:
            url = validated_url(request.url, query=True)
        except GenerationError:
            raise WorkflowError(
                "canvas_resource_url_invalid", "素材导入需要有效的 HTTP 或 HTTPS 地址", 422
            ) from None
        digest = content_hash({**request.model_dump(), "url": url, "canvas_key": canvas_key})
        previous, locator = None, None
        # 权限先于外部网络访问；不在数据库事务或项目锁内下载远程文件。
        with self._global_order_lock(), self._transaction():
            self._scope(canvas_key)
            if (
                CanvasResourceDeletionDAO(self.session).receipt(
                    user_id=self.actor_id, identity=identity
                )
                is not None
            ):
                raise WorkflowError(
                    "canvas_resource_deleted", "此导入资源已彻底删除，请明确重新导入", 410
                )
            old = self.resources.by_key(self.actor_id, identity)
            if old is not None:
                if (
                    old.mode != "multipart"
                    or old.declared_json.get("import_request_hash") != digest
                ):
                    raise WorkflowError(
                        "canvas_upload_conflict", "此请求标识已用于其他文件或来源", 409
                    )
                if old.status == "ready":
                    return self._ready(old)
                # 读取原对象前续期，防止清理器删除仍在恢复的暂存字节。
                old.expires_at = utcnow() + timedelta(minutes=90)
                previous = ImportedResourceStart.model_validate(old.declared_json)
                locator = old.storage_locator
        if previous is not None:
            location = self.location(locator)
            try:
                stored = self.storage.stat(location.bucket, location.object_name)
                if stored.size != previous.size or not 0 < stored.size <= MULTIPART_LIMIT:
                    raise WorkflowError(
                        "canvas_upload_conflict", "原导入对象长度不符，请保留原请求", 409
                    )
                with self.storage.open(location.bucket, location.object_name) as stream:
                    data = stream.read(MULTIPART_LIMIT + 1)
            except NotFound:
                data = None
            if data is not None:
                # 沿原校验与幂等哈希确认原字节，来源 URL 过期也不会再访问上游。
                return self.upload(
                    BytesIO(data), previous, previous.import_mime_type, key, canvas_key
                )
        try:
            data, mime = SafeTransport(self.settings).download_media(url, MULTIPART_LIMIT)
        except GenerationError as error:
            logger.warning(
                "Canvas resource import failed: kind=%s reason=%s", request.kind, error.code
            )
            if error.code == "response_too_large":
                raise WorkflowError(
                    "canvas_upload_too_large", "远程素材超过 50 MiB，请下载后使用分片上传", 413
                ) from None
            if error.code in {"unsafe_address", "invalid_redirect", "too_many_redirects"}:
                raise WorkflowError(
                    "canvas_resource_url_invalid", "远程素材地址或跳转地址不允许访问", 422
                ) from None
            raise WorkflowError(
                "canvas_resource_download_failed",
                "远程素材下载未完成，请保留原请求重试",
                503,
                details={"reason": error.code},
            ) from None
        if not data:
            raise WorkflowError("canvas_resource_invalid", "远程素材文件为空", 422)
        filename = self._filename(url, mime, request.kind)
        declared = ImportedResourceStart(
            file_name=filename,
            kind=request.kind,
            size=len(data),
            width=request.width,
            height=request.height,
            duration_ms=request.duration_ms,
            import_request_hash=digest,
            import_mime_type=mime or "application/octet-stream",
        )
        # upload 再次核验当前项目权限，并按实际图片/音视频检查结果写 MySQL/MinIO。
        return self.upload(BytesIO(data), declared, declared.import_mime_type, key, canvas_key)

    @staticmethod
    def _filename(url: str, mime: str, kind: str) -> str:
        filename = PurePosixPath(unquote(urlsplit(url).path)).name
        if not filename or filename in {".", ".."} or "." not in filename:
            extension = (
                mimetypes.guess_extension(mime)
                or {"image": ".png", "video": ".mp4", "audio": ".wav", "file": ".bin"}[kind]
            )
            filename = "resource" + extension
        if len(filename) > 255 or "\\" in filename or any(ord(c) < 32 for c in filename):
            raise WorkflowError("canvas_resource_invalid", "远程素材文件名无效", 422)
        return filename
