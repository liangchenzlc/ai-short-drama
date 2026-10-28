from typing import Annotated

from fastapi import APIRouter, Query

from short_drama.api.dependencies.assistant import Key, Tasks
from short_drama.schemas.assistant.conversations.history import (
    DiscussionEligibility,
    DiscussionRevisionRead,
    DiscussionRevisionRequest,
)
from short_drama.schemas.assistant.tasks.task import (
    TaskMessageAccepted,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse

router = APIRouter()


@router.get(
    "/global/conversations/{conversation_id}/messages/{message_id}/revision-eligibility",
    response_model=DiscussionEligibility,
)
def discussion_eligibility(conversation_id: Identifier, message_id: Identifier, service: Tasks):
    return service.discussion_eligibility(conversation_id, message_id)


@router.post(
    "/global/conversations/{conversation_id}/messages/{message_id}/revisions",
    response_model=TaskMessageAccepted,
    status_code=201,
)
def revise_discussion(
    conversation_id: Identifier,
    message_id: Identifier,
    payload: DiscussionRevisionRequest,
    service: Tasks,
    idempotency_key: Key,
):
    return service.revise_discussion(conversation_id, message_id, payload, idempotency_key)


@router.get(
    "/global/conversations/{conversation_id}/messages/{message_id}/revisions",
    response_model=PageResponse[DiscussionRevisionRead],
)
def discussion_revisions(
    conversation_id: Identifier,
    message_id: Identifier,
    service: Tasks,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    return service.discussion_revisions(conversation_id, message_id, offset, limit)
