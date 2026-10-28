from typing import Annotated

from fastapi import APIRouter, Query

from short_drama.api.dependencies.assistant import Key, Tasks
from short_drama.schemas.assistant.conversations.engine import (
    ConversationRead,
)
from short_drama.schemas.assistant.tasks.task import (
    DeletionMemoryPreview,
    GlobalConversationCreate,
    GlobalConversationDelete,
    GlobalConversationPatch,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse

router = APIRouter()


@router.get("/global/conversations", response_model=PageResponse[ConversationRead])
def global_conversations(
    service: Tasks,
    project_id: Identifier | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=120)] = None,
):
    return service.list_global_conversations(offset, limit, search=query, project_id=project_id)


@router.post("/global/conversations", response_model=ConversationRead, status_code=201)
def create_global_conversation(
    payload: GlobalConversationCreate, service: Tasks, idempotency_key: Key
):
    return service.create_global_conversation(payload, idempotency_key)


@router.get("/global/conversations/{conversation_id}", response_model=ConversationRead)
def global_conversation(conversation_id: Identifier, service: Tasks):
    return service.get_conversation(conversation_id)


@router.patch("/global/conversations/{conversation_id}", response_model=ConversationRead)
def rename_global_conversation(
    conversation_id: Identifier, payload: GlobalConversationPatch, service: Tasks
):
    return service.rename_global_conversation(conversation_id, payload)


@router.delete("/global/conversations/{conversation_id}", response_model=ConversationRead)
def delete_global_conversation(
    conversation_id: Identifier,
    payload: GlobalConversationDelete,
    service: Tasks,
    idempotency_key: Key,
):
    return service.delete_global_conversation(conversation_id, payload, idempotency_key)


@router.get(
    "/global/conversations/{conversation_id}/deletion-memory",
    response_model=DeletionMemoryPreview,
)
def deletion_memory_preview(
    conversation_id: Identifier,
    service: Tasks,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    return service.deletion_memory_preview(conversation_id, offset, limit)
