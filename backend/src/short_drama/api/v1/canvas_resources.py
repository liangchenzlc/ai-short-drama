"""Source-compatible resource routes backed by scoped MySQL and MinIO records."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Path, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from short_drama.api.v1.canvases import Canvases
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas_resource import (
    CanvasChunkRead,
    CanvasResourceCopyRequest,
    CanvasResourceEnvelope,
    CanvasResourceNormalizeRead,
    CanvasResourceNormalizeRequest,
    CanvasResourceStart,
    CanvasUploadRead,
    ResourceKind,
)
from short_drama.service.canvas_resource_io import resource_byte_range
from short_drama.service.canvas_resource_service import CHUNK_SIZE, CanvasResourceService

router = APIRouter(prefix="/canvas-runtime/resources", tags=["Canvas resources"])
ResourceId = Annotated[Identifier, Path()]
UploadKey = Annotated[str | None, Header(alias="X-Idempotency-Key", max_length=512)]
CanvasKey = Annotated[str | None, Header(alias="X-Canvas-Key", max_length=64)]


def get_resources(request: Request, canvases: Canvases):
    return CanvasResourceService(
        canvases.session, request.app.state.settings, request.app.state.storage
    )


Resources = Annotated[CanvasResourceService, Depends(get_resources)]


@router.post("/normalize", response_model=CanvasResourceNormalizeRead)
def normalize_resources(payload: CanvasResourceNormalizeRequest, service: Resources):
    from short_drama.service.canvas_resource_normalization import CanvasResourceNormalizationService

    normalizer = CanvasResourceNormalizationService(
        service.session, service.settings, service.storage
    )
    return normalizer.normalize(payload)


@router.post("/copies", response_model=CanvasResourceEnvelope)
def copy_resource(
    payload: CanvasResourceCopyRequest,
    service: Resources,
    key: Annotated[str, Header(alias="X-Idempotency-Key", min_length=1, max_length=512)],
):
    from short_drama.service.canvas_resource_copy import CanvasResourceCopyService

    copier = CanvasResourceCopyService(service.session, service.settings, service.storage)
    return {"resource": copier.copy(payload, key)}


@router.post("", response_model=CanvasResourceEnvelope)
def upload_resource(
    service: Resources,
    file: Annotated[UploadFile, File()],
    kind: Annotated[ResourceKind, Form()],
    width: Annotated[int | None, Form(ge=0, le=2**32 - 1)] = None,
    height: Annotated[int | None, Form(ge=0, le=2**32 - 1)] = None,
    duration_ms: Annotated[int | None, Form(alias="durationMs", ge=0, le=2**53 - 1)] = None,
    key: UploadKey = None,
    canvas_key: CanvasKey = None,
):
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if not size or not file.filename or len(file.filename) > 255:
        raise WorkflowError(
            "canvas_resource_invalid", "上传文件不能为空，文件名须在 255 字符以内", 422
        )
    request = CanvasResourceStart(
        file_name=file.filename,
        kind=kind,
        size=size,
        width=width,
        height=height,
        duration_ms=duration_ms,
    )
    return {
        "resource": service.upload(file.file, request, file.content_type or "", key, canvas_key)
    }


@router.post("/uploads", response_model=CanvasUploadRead)
def start_upload(
    payload: CanvasResourceStart,
    service: Resources,
    key: UploadKey = None,
    canvas_key: CanvasKey = None,
):
    return service.start(payload, key, canvas_key)


@router.put("/uploads/{upload_id}/chunks/{index}", response_model=CanvasChunkRead)
async def upload_chunk(
    upload_id: ResourceId, index: Annotated[int, Path(ge=0)], request: Request, service: Resources
):
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > CHUNK_SIZE:
            raise WorkflowError("canvas_upload_too_large", "分片超过 8 MiB", 413)
        body.extend(part)
    return await run_in_threadpool(service.put_chunk, upload_id, index, bytes(body))


@router.post("/uploads/{upload_id}/complete", response_model=CanvasResourceEnvelope)
def complete_upload(upload_id: ResourceId, service: Resources):
    return {"resource": service.complete(upload_id)}


@router.get("/{resource_id}", response_model=CanvasResourceEnvelope)
def read_resource(resource_id: ResourceId, service: Resources):
    resource, _ = service.read(resource_id)
    return {"resource": resource}


@router.get("/{resource_id}/file", operation_id="get_canvas_resource_file")
@router.head("/{resource_id}/file", operation_id="head_canvas_resource_file")
def resource_file(
    resource_id: ResourceId,
    request: Request,
    service: Resources,
    recycle_source_key: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    recycle_archive_key: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
):
    if recycle_source_key is not None or recycle_archive_key is not None:
        if recycle_source_key is None or recycle_archive_key is None:
            raise WorkflowError("canvas_recycle_invalid", "回收预览须提供完整删除记录", 422)
        from short_drama.service.canvas_recycle_service import CanvasRecycleService

        resource, locator = CanvasRecycleService(service.session).read_resource(
            recycle_source_key, recycle_archive_key, resource_id, service
        )
    else:
        resource, locator = service.read(resource_id)
    location = service.location(locator)
    stored = service.storage.stat(location.bucket, location.object_name)
    status, offset, length = resource_byte_range(request.headers.get("range"), stored.size)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:"
        ),
        "Content-Disposition": "attachment" if resource.kind == "file" else "inline",
    }
    if resource.etag:
        headers["ETag"] = '"' + resource.etag + '"'
    if status == 206:
        headers["Content-Range"] = f"bytes {offset}-{offset + length - 1}/{stored.size}"
    elif status == 416:
        headers["Content-Range"] = f"bytes */{stored.size}"
    if request.method == "HEAD" or status == 416 or length == 0:
        return Response(status_code=status, headers=headers, media_type=resource.mime_type)
    context = service.storage.open(
        location.bucket, location.object_name, offset=offset, length=length
    )
    response = context.__enter__()
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            context.__exit__(None, None, None)

    def chunks():
        try:
            yield from response.stream(256 * 1024)
        finally:
            close()

    return StreamingResponse(
        chunks(),
        status_code=status,
        headers=headers,
        media_type=resource.mime_type,
        background=BackgroundTask(close),
    )
