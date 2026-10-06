from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from pydantic import ValidationError

from short_drama.api.v1.canvases import Canvases
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas_library import (
    CanvasLibraryBatch,
    CanvasLibraryBatchRead,
    CanvasLibraryDeleteRead,
    CanvasLibraryFilter,
    CanvasLibraryFolderEnvelope,
    CanvasLibraryFoldersRead,
    CanvasLibraryFolderWrite,
    CanvasLibraryGetRead,
    CanvasLibraryListRead,
    CanvasLibraryMove,
    CanvasLibraryPageRead,
    CanvasLibraryPut,
    CanvasLibraryPutRead,
)
from short_drama.service.canvas_library_service import CanvasLibraryService

router = APIRouter(prefix="/canvas-runtime/assets", tags=["Canvas asset library"])
folder_router = APIRouter(prefix="/canvas-runtime/asset-folders", tags=["Canvas asset library"])
AssetKey = Annotated[str, Path(min_length=1, max_length=80)]
FolderId = Annotated[Identifier, Path()]


@folder_router.get("", response_model=CanvasLibraryFoldersRead)
def list_folders(service: Canvases):
    return {"folders": CanvasLibraryService(service.session).folders()}


@folder_router.post("", response_model=CanvasLibraryFolderEnvelope)
def create_folder(payload: CanvasLibraryFolderWrite, service: Canvases):
    return {"folder": CanvasLibraryService(service.session).save_folder(payload)}


@folder_router.patch("/{folder_id}", response_model=CanvasLibraryFolderEnvelope)
def rename_folder(folder_id: FolderId, payload: CanvasLibraryFolderWrite, service: Canvases):
    return {"folder": CanvasLibraryService(service.session).save_folder(payload, folder_id)}


@folder_router.delete("/{folder_id}")
def delete_folder(folder_id: FolderId, service: Canvases):
    CanvasLibraryService(service.session).delete_folder(folder_id)
    return {"id": str(folder_id)}


@router.get("", response_model=CanvasLibraryListRead | CanvasLibraryPageRead)
def list_assets(request: Request, service: Canvases):
    if "page" in request.query_params:
        try:
            filters = CanvasLibraryFilter.model_validate(dict(request.query_params))
        except ValidationError:
            raise WorkflowError("canvas_asset_invalid", "素材查询参数无效", 422) from None
        return CanvasLibraryService(service.session).page(filters)
    return {"assets": CanvasLibraryService(service.session).list()}


@router.post("/batch", response_model=CanvasLibraryBatchRead)
def lookup_assets(payload: CanvasLibraryBatch, service: Canvases):
    return {"assets": CanvasLibraryService(service.session).batch(payload.ids)}


@router.patch("/folder", response_model=CanvasLibraryMove)
def move_assets(payload: CanvasLibraryMove, service: Canvases):
    return CanvasLibraryService(service.session).move(payload)


@router.get("/{asset_key}", response_model=CanvasLibraryGetRead)
def read_asset(asset_key: AssetKey, service: Canvases):
    return {"asset": CanvasLibraryService(service.session).read(asset_key)}


@router.put("/{asset_key}", response_model=CanvasLibraryPutRead)
def put_asset(asset_key: AssetKey, payload: CanvasLibraryPut, service: Canvases):
    return {"asset": CanvasLibraryService(service.session).put(asset_key, payload.asset)}


@router.delete("/{asset_key}", response_model=CanvasLibraryDeleteRead)
def delete_asset(
    asset_key: AssetKey,
    service: Canvases,
    expected_status: Annotated[str, Query(alias="expectedStatus", max_length=32)] = "",
):
    CanvasLibraryService(service.session).delete_asset(asset_key, expected_status)
    return {"id": asset_key}
