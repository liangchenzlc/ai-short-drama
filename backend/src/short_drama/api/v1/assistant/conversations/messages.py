from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

from short_drama.api.dependencies.assistant import Key, Tasks
from short_drama.api.v1.assistant.conversations.events import conversation_events, event_cursor
from short_drama.schemas.assistant.operations.runtime import (
    ConversationRuntimeState,
    MessageRead,
)
from short_drama.schemas.assistant.tasks.task import (
    AssistantTaskMessage,
    TaskMessageAccepted,
    TaskRuntimeState,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse

router = APIRouter()


@router.post(
    "/global/conversations/{conversation_id}/messages",
    response_model=TaskMessageAccepted,
    status_code=201,
)
def send_task_message(
    conversation_id: Identifier, payload: AssistantTaskMessage, service: Tasks, idempotency_key: Key
):
    return service.send_task_message(conversation_id, payload, idempotency_key)


@router.get(
    "/global/conversations/{conversation_id}/messages", response_model=PageResponse[MessageRead]
)
def global_messages(
    conversation_id: Identifier,
    service: Tasks,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return service.list_messages(conversation_id, offset, limit)


@router.get("/global/conversations/{conversation_id}/state", response_model=TaskRuntimeState)
def global_state(
    conversation_id: Identifier,
    service: Tasks,
    before_task_id: Identifier | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.task_runtime_state(conversation_id, before_task_id=before_task_id, limit=limit)


@router.get(
    "/global/conversations/{conversation_id}/runtime", response_model=ConversationRuntimeState
)
def global_runtime(conversation_id: Identifier, service: Tasks):
    return service.runtime_state(conversation_id)


@router.get("/global/conversations/{conversation_id}/events")
async def global_events(
    conversation_id: Identifier,
    request: Request,
    service: Tasks,
    cursor: Annotated[int, Query(ge=0, le=2**64 - 1)] = 0,
    last_event_id: Annotated[str | None, Header(max_length=20)] = None,
):
    # 游标先验证，再读取私有会话；无效重连请求不应触发数据库访问。
    cursor = event_cursor(cursor, last_event_id)
    service.get_conversation(conversation_id)
    return await conversation_events(conversation_id, request, cursor, last_event_id)
