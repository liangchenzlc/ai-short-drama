"""冻结图中声明的绘图版本；恢复不读取最新笔画来冒充历史。"""

import re
from copy import deepcopy

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.canvas_drawing_dao import CanvasDrawingDAO
from short_drama.domain import CanvasRevisionDrawingReference
from short_drama.schemas.base import UINT64_MAX

from .base import utcnow
from .canvas_document import content_hash
from .canvas_drawing_document import drawing_document


def drawing_nodes(document):
    for node in document.get("nodes", []):
        if node.get("type") == "drawing" and node.get("metadata", {}).get("drawingId"):
            yield node["metadata"]


def declared_versions(document):
    result = {}
    for metadata in drawing_nodes(document):
        key = metadata["drawingId"]
        revision = metadata.get("drawingRevision", "0")
        if (
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 128
            or isinstance(revision, bool)
            or not re.fullmatch(r"0|[1-9][0-9]{0,19}", str(revision))
            or int(revision) > UINT64_MAX
        ):
            raise WorkflowError("canvas_drawing_invalid", "绘图版本身份无效", 422)
        if int(revision) == 0:
            continue
        if key in result and result[key] != int(revision):
            raise WorkflowError("canvas_drawing_invalid", "同一绘图在图中引用了不同版本", 422)
        result[key] = int(revision)
    return result


def apply_drawing_metadata(metadata, version):
    saved = drawing_document(version)
    metadata.update(
        drawingRevision=str(version.row_version),
        drawingEngine=saved["engine"],
        drawingUpdatedAt=version.created_at.isoformat(timespec="microseconds") + "Z",
        drawingShapeCount=saved["shapeCount"],
        drawingPageCount=saved["pageCount"],
    )
    preview = saved.get("previewResourceId")
    if preview:
        metadata["drawingPreviewStorageKey"] = f"resource:{preview}"
        metadata["drawingPreviewUrl"] = f"/api/v1/canvas-runtime/resources/{preview}/file"
    else:
        metadata.pop("drawingPreviewStorageKey", None)
        metadata.pop("drawingPreviewUrl", None)


def freeze_drawings(service, canvas_id, document, *, lock=False, current=False):
    frozen = deepcopy(document)
    dao = CanvasDrawingDAO(service.session)
    versions = declared_versions(frozen)
    if current:
        heads = dao.heads(canvas_id, lock=lock)
        for metadata in drawing_nodes(frozen):
            key = metadata["drawingId"]
            if key in heads and not heads[key]["deleted"]:
                versions[key] = int(heads[key]["revision"])
    found = dao.document_versions(canvas_id, versions, lock=lock)
    for metadata in drawing_nodes(frozen):
        pair = found.get(metadata["drawingId"])
        if pair:
            apply_drawing_metadata(metadata, pair[1])
    return frozen, found


def bind_revision_drawings(service, canvas, revision, versions):
    for _, version in versions.values():
        service.session.add(
            CanvasRevisionDrawingReference(
                **service.child(canvas), revision_id=revision.id, drawing_version_id=version.id
            )
        )


def check_drawing_heads(service, canvas, expected):
    current = CanvasDrawingDAO(service.session).heads(canvas.id, lock=True)
    if expected is None and not current:
        return
    if expected != current:
        raise WorkflowError(
            "canvas_drawing_restore_conflict",
            "绘图在打开版本记录后已有更新，请刷新版本记录后重新确认恢复；本机草稿已保留",
            409,
        )


def restore_revision_drawings(service, canvas, revision, document):
    from .canvas_drawing_service import CanvasDrawingService

    dao = CanvasDrawingDAO(service.session)
    versions = declared_versions(document)
    found = dao.document_versions(canvas.id, versions, lock=True)
    bindings = dao.revision_bindings(canvas.id, revision.id)
    if set(versions) != set(found) or (
        bindings
        and {row.drawing_version_id for row in bindings} != {v.id for _, v in found.values()}
    ):
        raise WorkflowError(
            "canvas_drawing_history_missing",
            "历史绘图版本缺失或与冻结引用不一致，无法完整恢复",
            409,
        )
    drawings = CanvasDrawingService(service.session)
    restored = {}
    for key, (drawing, frozen) in found.items():
        current = dao.version(drawing.id, drawing.row_version, lock=True)
        original = drawing_document(frozen)
        if (
            drawing.archived_at is None
            and current
            and content_hash(drawing_document(current)) == content_hash(original)
        ):
            restored[key] = current
            continue
        if drawing.row_version == UINT64_MAX:
            raise WorkflowError("canvas_drawing_version_exhausted", "绘图版本已达上限", 409)
        resources = drawings._resources(canvas, original)
        drawing.row_version += 1
        drawing.archived_at = None
        drawing.updated_at = utcnow()
        drawing.updated_by = service.actor_id
        restored[key] = drawings.append_version(canvas, drawing, original, resources)
    for metadata in drawing_nodes(document):
        if metadata["drawingId"] in restored:
            apply_drawing_metadata(metadata, restored[metadata["drawingId"]])
