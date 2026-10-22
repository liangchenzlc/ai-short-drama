"""Owner-only memory management; task tools derive their narrower scopes independently."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.schemas.agent_memory import (
    MemoryClarificationChoice,
    MemoryContextRead,
    MemoryCreate,
    MemoryJobRead,
    MemoryJobRetry,
    MemoryPatch,
    MemoryRead,
    MemoryReviewJobRead,
    MemorySummaryRead,
    MemoryVersion,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.service.agent_memory_service import AgentMemoryService

router = APIRouter(prefix="/memory")


def service(session: Session = Depends(get_session)):
    return AgentMemoryService(session)


Memories = Annotated[AgentMemoryService, Depends(service)]


@router.get("", response_model=PageResponse[MemoryRead])
def list_memories(
    service: Memories,
    conversation_id: Identifier | None = None,
    project_id: Identifier | None = None,
    query: Annotated[str | None, Query(max_length=120)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    include_forgotten: bool = False,
):
    return service.list_items(
        conversation_id=conversation_id,
        project_id=project_id,
        query=query,
        offset=offset,
        limit=limit,
        include_forgotten=include_forgotten,
    )


@router.post("", response_model=MemoryRead, status_code=201)
def create_memory(
    payload: MemoryCreate,
    service: Memories,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")
    ],
):
    return service.create_item(payload, idempotency_key)


@router.get("/{memory_id}", response_model=MemoryRead)
def read_memory(memory_id: Identifier, service: Memories):
    return service.get_item(memory_id)


@router.get("/conversations/{conversation_id}/context", response_model=MemoryContextRead)
def conversation_memory_context(conversation_id: Identifier, service: Memories):
    return service.conversation_context(conversation_id)


@router.get(
    "/conversations/{conversation_id}/summaries/{summary_id}", response_model=MemorySummaryRead
)
def read_conversation_summary(
    conversation_id: Identifier, summary_id: Identifier, service: Memories
):
    return service.read_summary(conversation_id, summary_id)


@router.get(
    "/conversations/{conversation_id}/reviews", response_model=PageResponse[MemoryReviewJobRead]
)
def review_memory_jobs(
    conversation_id: Identifier,
    service: Memories,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.review_jobs(conversation_id, offset, limit)


@router.post("/jobs/{job_id}/clarifications/{index}", response_model=MemoryReviewJobRead)
def resolve_memory_clarification(
    job_id: Identifier,
    index: Annotated[int, Path(ge=0, le=11)],
    payload: MemoryClarificationChoice,
    service: Memories,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")
    ],
):
    return service.resolve_clarification(job_id, index, payload, idempotency_key)


@router.post("/jobs/{job_id}/retry", response_model=MemoryJobRead, status_code=201)
def retry_memory_job(
    job_id: Identifier,
    payload: MemoryJobRetry,
    service: Memories,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")
    ],
):
    return service.retry_job(job_id, payload, idempotency_key)


@router.patch("/{memory_id}", response_model=MemoryRead)
def patch_memory(memory_id: Identifier, payload: MemoryPatch, service: Memories):
    return service.patch_item(memory_id, payload)


@router.delete("/{memory_id}", response_model=MemoryRead)
def forget_memory(memory_id: Identifier, payload: MemoryVersion, service: Memories):
    return service.forget_item(memory_id, payload)


@router.post("/{memory_id}/restore", response_model=MemoryRead)
def restore_memory(memory_id: Identifier, payload: MemoryVersion, service: Memories):
    return service.restore_item(memory_id, payload)


@router.get("/{memory_id}/revisions")
def memory_revisions(memory_id: Identifier, service: Memories):
    return {"items": service.revisions(memory_id)}
