from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile

from short_drama.api.dependencies.assistant import Attachments, Key, Tasks
from short_drama.schemas.assistant.context.context import AttachmentRead, AttachmentReference
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse

router = APIRouter()


@router.get(
    "/conversations/{conversation_id}/attachments", response_model=PageResponse[AttachmentRead]
)
def attachments(
    conversation_id: Identifier,
    service: Tasks,
    attachments: Attachments,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    pending: bool | None = None,
):
    service.validate_attachment_conversation(conversation_id)
    return attachments.list(conversation_id, offset, limit, pending)


@router.post(
    "/conversations/{conversation_id}/attachments/uploads",
    response_model=AttachmentRead,
    status_code=201,
)
def upload_attachment(
    conversation_id: Identifier,
    service: Tasks,
    attachments: Attachments,
    file: Annotated[UploadFile, File()],
    idempotency_key: Key,
):
    service.validate_attachment_conversation(conversation_id)
    return attachments.upload(conversation_id, file.file, file.filename or "", idempotency_key)


@router.post(
    "/conversations/{conversation_id}/attachments/references",
    response_model=AttachmentRead,
    status_code=201,
)
def reference_attachment(
    conversation_id: Identifier,
    payload: AttachmentReference,
    service: Tasks,
    attachments: Attachments,
    idempotency_key: Key,
):
    service.validate_attachment_conversation(conversation_id)
    return attachments.reference(conversation_id, payload, idempotency_key)


@router.delete("/conversations/{conversation_id}/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    conversation_id: Identifier,
    attachment_id: Identifier,
    service: Tasks,
    attachments: Attachments,
):
    service.validate_attachment_conversation(conversation_id)
    attachments.delete(conversation_id, attachment_id)
