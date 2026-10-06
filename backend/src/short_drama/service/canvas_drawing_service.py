"""源绘图 CAS 与重放语义；共享已保存作品，私人草稿留在客户端。"""

from copy import deepcopy

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_drawing_dao import CanvasDrawingDAO
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.domain import CanvasDrawing, CanvasDrawingMediaReference, CanvasDrawingVersion
from short_drama.domain.media_file import MediaFile
from short_drama.schemas.canvas_drawing import CanvasDrawingWrite

from .base import BaseService, utcnow
from .canvas_document import content_hash
from .canvas_drawing_document import drawing_document, drawing_resource_ids, stored_drawing_document
from .canvas_service import CanvasService, iso
from .publication import publish


class CanvasDrawingService(BaseService):
    model = CanvasDrawing

    def __init__(self, session):
        super().__init__(session)
        self.drawings = CanvasDrawingDAO(session)
        self.canvases = CanvasService(session)
        self.resources = CanvasResourceDAO(session)

    def _canvas(self, key, *, lock=False):
        canvas = self.drawings.canvas(key)
        if canvas is None:
            raise NotFound("画布不存在或已无权访问")
        return self.canvases.require_canvas(canvas.project_id, canvas.id, lock=lock)

    @staticmethod
    def _read(drawing, version, *, include_snapshot=True):
        document = drawing_document(version)
        if not include_snapshot:
            document.pop("snapshot", None)
        return {
            **document,
            "drawingId": drawing.source_key,
            "revision": str(version.row_version),
            "createdAt": iso(drawing.created_at),
            "updatedAt": iso(version.created_at),
        }

    def list(self, canvas_key):
        with self._transaction(read_only=True):
            canvas = self._canvas(canvas_key)
            return {
                "drawings": [
                    self._read(drawing, version, include_snapshot=False)
                    for drawing, version in self.drawings.active(canvas.id)
                ]
            }

    def get(self, canvas_key, drawing_key, revision=None):
        with self._transaction(read_only=True):
            canvas = self._canvas(canvas_key)
            drawing = self.drawings.drawing(canvas.id, drawing_key)
            if drawing is None or (drawing.archived_at is not None and revision is None):
                raise NotFound("绘图不存在")
            version = self.drawings.version(drawing.id, revision or drawing.row_version)
            if version is None:
                raise NotFound("绘图版本不存在")
            return {"drawing": self._read(drawing, version)}

    def _document(self, request, current):
        document = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        document.pop("drawingId")
        document.pop("revision")
        if request.render is None and current and "render" in current.document_json:
            document["render"] = deepcopy(current.document_json["render"])
        render = document.get("render")
        if render is not None:
            identifier = render.get("resourceId")
            expected = f"resource:{identifier}" if identifier else ""
            if render.get("storageKey") and render["storageKey"] != expected:
                raise WorkflowError("canvas_drawing_invalid", "绘图成品资源身份不一致", 422)
            render["storageKey"] = expected
        return document

    def _resources(self, canvas, document):
        identifiers = drawing_resource_ids(document)
        resources = []
        for identifier in sorted(identifiers):
            resource = self.resources.resource(identifier, lock=True)
            if (
                not isinstance(resource, MediaFile)
                or resource.project_id != canvas.project_id
                or resource.published_at is None
                and resource.created_by != self.canvases.actor_id
            ):
                raise NotFound("绘图资源不存在或不属于当前项目")
            if not resource.format_code.startswith("image/") or not resource.byte_size:
                raise WorkflowError("canvas_drawing_invalid", "绘图只能引用已保存的图片资源", 422)
            resources.append(resource)
        return resources

    def put(self, canvas_key, drawing_key, payload):
        request = CanvasDrawingWrite.model_validate(
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, CanvasDrawingWrite)
            else payload
        )
        if request.drawing_id != drawing_key:
            raise WorkflowError("canvas_drawing_invalid", "绘图 ID 与请求路径不一致", 422)
        with self._transaction():
            canvas = self._canvas(canvas_key, lock=True)
            drawing = self.drawings.drawing(canvas.id, drawing_key, lock=True)
            if drawing and drawing.archived_at is not None:
                raise WorkflowError("canvas_drawing_deleted", "绘图已删除，不能重新导入", 409)
            current = (
                self.drawings.version(drawing.id, drawing.row_version, lock=True)
                if drawing
                else None
            )
            document = self._document(request, current)
            if (
                drawing
                and current
                and request.revision + 1 == drawing.row_version
                and content_hash(document) == content_hash(drawing_document(current))
            ):
                return {"drawing": self._read(drawing, current)}
            if (drawing is None and request.revision != 0) or (
                drawing and drawing.row_version != request.revision
            ):
                raise WorkflowError(
                    "canvas_drawing_revision_conflict",
                    "绘图已有更新，已停止覆盖；请保留本机草稿并加载最新版本",
                    409,
                    {"current_version": str(drawing.row_version) if drawing else "0"},
                )
            resources = self._resources(canvas, document)
            if drawing is None:
                drawing = CanvasDrawing(
                    **self.canvases.child(canvas),
                    source_key=drawing_key,
                    row_version=1,
                    archived_at=None,
                )
                self.session.add(drawing)
                self.session.flush()
            else:
                drawing.row_version += 1
                drawing.updated_at = utcnow()
                drawing.updated_by = self.canvases.actor_id
            version = self.append_version(canvas, drawing, document, resources)
            return {"drawing": self._read(drawing, version)}

    def append_version(self, canvas, drawing, document, resources):
        """调用方须持有项目锁并管理事务；历史恢复与普通保存使用相同版本写入。"""
        version = CanvasDrawingVersion(
            **self.canvases.child(canvas),
            drawing_id=drawing.id,
            row_version=drawing.row_version,
            document_json=stored_drawing_document(document),
        )
        self.session.add(version)
        self.session.flush()
        for resource in resources:
            publish(resource)
            self.session.add(
                CanvasDrawingMediaReference(
                    **self.canvases.child(canvas),
                    drawing_version_id=version.id,
                    media_id=resource.id,
                )
            )
        self.session.flush()
        return version

    def delete(self, canvas_key, drawing_key):
        with self._transaction():
            canvas = self._canvas(canvas_key, lock=True)
            drawing = self.drawings.drawing(canvas.id, drawing_key, lock=True)
            if drawing is None:
                raise NotFound("绘图不存在")
            if drawing.archived_at is None:
                drawing.archived_at = drawing.updated_at = utcnow()
                drawing.updated_by = self.canvases.actor_id
            return {"id": drawing_key, "revision": str(drawing.row_version)}
