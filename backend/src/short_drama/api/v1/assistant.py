"""One authenticated project-chat API for standard and canvas workspaces."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.api.v1.agent import agent_status, conversation_events
from short_drama.schemas.agent import AgentStatus, ConversationPatch, ConversationRead
from short_drama.schemas.agent_context import AttachmentRead, AttachmentReference
from short_drama.schemas.agent_runtime import (
    ConversationRuntimeState,
    MessageAccepted,
    MessageRead,
    RunRead,
)
from short_drama.schemas.assistant import AssistantConversationCreate, AssistantMessageCreate
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.service.agent_attachment_service import AgentAttachmentService
from short_drama.service.assistant_service import AssistantService

router = APIRouter(prefix="/assistant", tags=["AI creative assistant"])
Key = Annotated[str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")]


def service(request: Request, session: Session = Depends(get_session)):
    return AssistantService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


def attachment_service(request: Request, session: Session = Depends(get_session)):
    return AgentAttachmentService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


Assistants = Annotated[AssistantService, Depends(service)]
Attachments = Annotated[AgentAttachmentService, Depends(attachment_service)]


@router.get("/status", response_model=AgentStatus)
def status(request: Request):
    return agent_status(request)


@router.get("/conversations", response_model=PageResponse[ConversationRead])
def conversations(
    service: Assistants,
    project_id: Identifier,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_archived: bool = False,
    query: Annotated[str | None, Query(max_length=120)] = None,
):
    return service.list_project_conversations(
        project_id, offset, limit, include_archived=include_archived, search=query
    )


@router.get("/legacy-conversations", response_model=PageResponse[ConversationRead])
def legacy_conversations(
    service: Assistants,
    project_id: Identifier,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_archived: bool = True,
    query: Annotated[str | None, Query(max_length=120)] = None,
):
    return service.list_project_conversations(
        project_id, offset, limit, legacy=True, include_archived=include_archived, search=query
    )


@router.post("/conversations", response_model=ConversationRead, status_code=201)
def create_conversation(
    payload: AssistantConversationCreate, service: Assistants, idempotency_key: Key
):
    return service.create_project_conversation(payload, idempotency_key)


@router.post("/conversations/resolve", response_model=ConversationRead)
def resolve_conversation(payload: AssistantConversationCreate, service: Assistants):
    return service.create_project_conversation(payload, resolve=True)


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation(conversation_id: Identifier, service: Assistants):
    return service.get_conversation(conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
def patch_conversation(
    conversation_id: Identifier, payload: ConversationPatch, service: Assistants
):
    return service.patch_conversation(conversation_id, payload)


@router.post(
    "/conversations/{conversation_id}/messages", response_model=MessageAccepted, status_code=201
)
def send_message(
    conversation_id: Identifier,
    payload: AssistantMessageCreate,
    service: Assistants,
    idempotency_key: Key,
):
    return service.send_message(conversation_id, payload, idempotency_key)


@router.get("/conversations/{conversation_id}/messages", response_model=PageResponse[MessageRead])
def messages(
    conversation_id: Identifier,
    service: Assistants,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return service.list_messages(conversation_id, offset, limit)


@router.get("/conversations/{conversation_id}/runs", response_model=PageResponse[RunRead])
def runs(
    conversation_id: Identifier,
    service: Assistants,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.list_runs(conversation_id, offset, limit)


@router.get("/conversations/{conversation_id}/state", response_model=ConversationRuntimeState)
def state(conversation_id: Identifier, service: Assistants):
    return service.runtime_state(conversation_id)


@router.get("/conversations/{conversation_id}/events")
async def events(
    conversation_id: Identifier,
    request: Request,
    service: Assistants,
    cursor: Annotated[int, Query(ge=0, le=2**64 - 1)] = 0,
    last_event_id: Annotated[str | None, Header(max_length=20)] = None,
):
    service.get_conversation(conversation_id)
    return await conversation_events(conversation_id, request, None, cursor, last_event_id)


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(run_id: Identifier, service: Assistants):
    return service.get_run(run_id)


@router.post("/runs/{run_id}/stop", response_model=RunRead)
def stop(run_id: Identifier, service: Assistants):
    return service.stop(run_id)


@router.get(
    "/conversations/{conversation_id}/attachments", response_model=PageResponse[AttachmentRead]
)
def attachments(
    conversation_id: Identifier,
    service: Assistants,
    attachments: Attachments,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    pending: bool | None = None,
):
    service.get_conversation(conversation_id)
    return attachments.list(conversation_id, offset, limit, pending)


@router.post(
    "/conversations/{conversation_id}/attachments/uploads",
    response_model=AttachmentRead,
    status_code=201,
)
def upload_attachment(
    conversation_id: Identifier,
    service: Assistants,
    attachments: Attachments,
    file: Annotated[UploadFile, File()],
    idempotency_key: Key,
):
    service.get_conversation(conversation_id)
    return attachments.upload(conversation_id, file.file, file.filename or "", idempotency_key)


@router.post(
    "/conversations/{conversation_id}/attachments/references",
    response_model=AttachmentRead,
    status_code=201,
)
def reference_attachment(
    conversation_id: Identifier,
    payload: AttachmentReference,
    service: Assistants,
    attachments: Attachments,
    idempotency_key: Key,
):
    service.get_conversation(conversation_id)
    return attachments.reference(conversation_id, payload, idempotency_key)


@router.delete("/conversations/{conversation_id}/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    conversation_id: Identifier,
    attachment_id: Identifier,
    service: Assistants,
    attachments: Attachments,
):
    service.get_conversation(conversation_id)
    attachments.delete(conversation_id, attachment_id)
