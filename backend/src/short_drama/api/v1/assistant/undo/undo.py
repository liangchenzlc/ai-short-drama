from fastapi import APIRouter

from short_drama.api.dependencies.assistant import Key, Tasks
from short_drama.schemas.assistant.undo.undo import (
    BatchUndoPreview,
    BatchUndoRead,
    BatchUndoRequest,
)
from short_drama.schemas.base import Identifier

router = APIRouter()


@router.get("/operations/{operation_id}/undo-preview", response_model=BatchUndoPreview)
def undo_preview(operation_id: Identifier, service: Tasks):
    return service.undo_preview(operation_id)


@router.post("/operations/{operation_id}/undo", response_model=BatchUndoRead, status_code=201)
def undo_batch(
    operation_id: Identifier, payload: BatchUndoRequest, service: Tasks, idempotency_key: Key
):
    return service.undo_batch(operation_id, payload, idempotency_key)
