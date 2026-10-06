from typing import Annotated

from fastapi import APIRouter, Path

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.canvas_folder import (
    CanvasFolderDeleted,
    CanvasFolderEnvelope,
    CanvasFolderPut,
    CanvasFoldersRead,
    FolderKey,
)
from short_drama.service.canvas_folder_service import CanvasFolderService

router = APIRouter(prefix="/canvas-runtime/canvas-folders", tags=["Canvas project folders"])
Key = Annotated[FolderKey, Path()]


@router.get("", response_model=CanvasFoldersRead, response_model_exclude_none=True)
def list_folders(service: Canvases):
    return {"folders": CanvasFolderService(service.session).list()}


@router.put("/{key}", response_model=CanvasFolderEnvelope, response_model_exclude_none=True)
def put_folder(key: Key, payload: CanvasFolderPut, service: Canvases):
    return {"folder": CanvasFolderService(service.session).put(key, payload.folder)}


@router.delete("/{key}", response_model=CanvasFolderDeleted)
def delete_folder(key: Key, service: Canvases):
    CanvasFolderService(service.session).delete(key)
    return {"id": key}
