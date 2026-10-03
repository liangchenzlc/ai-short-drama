"""Authenticated Agent entry points. Visiting this API never invokes a model."""

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.schemas.agent import (
    AgentStatus,
    ConversationCreate,
    ConversationPatch,
    ConversationRead,
)
from short_drama.schemas.agent_runtime import (
    AgentModelRead,
    AgentModelsRead,
    MessageAccepted,
    MessageCreate,
    MessageRead,
    ModelVerify,
    ReviewDecision,
    RunContinue,
    RunRead,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.service.agent_conversation_service import AgentConversationService
from short_drama.service.agent_model_service import AgentModelService
from short_drama.service.agent_run_service import AgentRunService

router = APIRouter(prefix="/agent", tags=["Agent creation"])


def service(request: Request, session: Session = Depends(get_session)):
    return AgentConversationService(session, request.app.state.settings)


Conversations = Annotated[AgentConversationService, Depends(service)]


def run_service(request: Request, session: Session = Depends(get_session)):
    return AgentRunService(session, request.app.state.settings)


def model_service(request: Request, session: Session = Depends(get_session)):
    return AgentModelService(session, request.app.state.settings)


Runs = Annotated[AgentRunService, Depends(run_service)]
Models = Annotated[AgentModelService, Depends(model_service)]


@router.get("/status", response_model=AgentStatus)
def agent_status(request: Request):
    return AgentStatus(
        enabled=request.app.state.settings.agent_enabled,
        schema_ready=getattr(request.app.state, "agent_schema_ready", False),
    )


@router.get("/conversations", response_model=PageResponse[ConversationRead])
def list_conversations(
    service: Conversations,
    project_id: Identifier,
    episode_id: Identifier,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_archived: bool = False,
):
    return service.list_conversations(project_id, episode_id, offset, limit, include_archived)


@router.post("/conversations", response_model=ConversationRead, status_code=201)
def create_conversation(
    payload: ConversationCreate,
    service: Conversations,
    idempotency_key: Annotated[str | None, Header(max_length=64)] = None,
):
    return service.create_conversation(payload, idempotency_key)


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation(conversation_id: Identifier, service: Conversations):
    return service.get_conversation(conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
def patch_conversation(
    conversation_id: Identifier, payload: ConversationPatch, service: Conversations
):
    return service.patch_conversation(conversation_id, payload)


@router.get("/models", response_model=AgentModelsRead)
def list_models(service: Models):
    return service.list_models()


@router.post("/models/{model_id}/verify", response_model=AgentModelRead)
def verify_model(model_id: Identifier, payload: ModelVerify, service: Models):
    return service.verify(model_id, payload)


@router.post(
    "/conversations/{conversation_id}/messages", response_model=MessageAccepted, status_code=201
)
def send_message(
    conversation_id: Identifier,
    payload: MessageCreate,
    service: Runs,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=64)],
):
    return service.send_message(conversation_id, payload, idempotency_key)


@router.get("/conversations/{conversation_id}/messages", response_model=PageResponse[MessageRead])
def list_messages(
    conversation_id: Identifier,
    service: Runs,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return service.list_messages(conversation_id, offset, limit)


@router.get("/conversations/{conversation_id}/runs", response_model=PageResponse[RunRead])
def list_runs(
    conversation_id: Identifier,
    service: Runs,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.list_runs(conversation_id, offset, limit)


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(run_id: Identifier, service: Runs):
    return service.get_run(run_id)


@router.post("/runs/{run_id}/stop", response_model=RunRead)
def stop_run(run_id: Identifier, service: Runs):
    return service.stop(run_id)


@router.post("/runs/{run_id}/reviews/{tool_call_id}", response_model=RunRead)
def review_plan(
    run_id: Identifier, tool_call_id: Identifier, payload: ReviewDecision, service: Runs
):
    return service.review(run_id, tool_call_id, payload)


@router.get("/conversations/{conversation_id}/events")
async def conversation_events(
    conversation_id: Identifier,
    request: Request,
    cursor: Annotated[int, Query(ge=0, le=2**64 - 1)] = 0,
    last_event_id: Annotated[str | None, Header(max_length=20)] = None,
):
    if last_event_id is not None:
        if (
            not last_event_id.isascii()
            or not last_event_id.isdecimal()
            or int(last_event_id) > 2**64 - 1
        ):
            raise WorkflowError("invalid_event_cursor", "Invalid event cursor", 422)
        cursor = max(cursor, int(last_event_id))
    factory = request.app.state.session_factory
    settings = request.app.state.settings
    actor = getattr(request.state, "actor", None)

    def read(after):
        with factory() as session:
            session.info["actor"] = actor
            return AgentRunService(session, settings).events(
                conversation_id, after, verify_session=True
            )

    initial = await run_in_threadpool(read, cursor)

    async def stream():
        after, rows, heartbeat = cursor, initial, time.monotonic()
        while not await request.is_disconnected():
            for event in rows:
                yield f"id: {event.seq}\nevent: agent\ndata: {event.model_dump_json()}\n\n"
                after = event.seq
            if time.monotonic() - heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(1)
            try:
                rows = await run_in_threadpool(read, after)
            except BusinessError:
                # No private payload after logout, expiry, removal or feature shutdown.
                yield 'event: access-ended\ndata: {"code":"agent_access_ended"}\n\n'
                return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/continue", response_model=RunRead)
def continue_run(run_id: Identifier, payload: RunContinue, service: Runs):
    return service.continue_after_adoption(run_id, payload)
