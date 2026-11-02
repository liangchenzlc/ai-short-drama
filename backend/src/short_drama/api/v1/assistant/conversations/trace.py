from typing import Annotated

from fastapi import APIRouter, Query

from short_drama.api.dependencies.assistant import Traces
from short_drama.schemas.assistant.operations.trace import TracePage, TraceResourceRead
from short_drama.schemas.base import Identifier

router = APIRouter()
PATH = "/conversations/{conversation_id}/runs/{run_id}/trace"


@router.get(PATH, response_model=TracePage)
def trace_page(
    conversation_id: Identifier,
    run_id: Identifier,
    service: Traces,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return service.page(conversation_id, run_id, before_seq, limit)


@router.get(PATH + "/{block_id}/resources/{resource_id}", response_model=TraceResourceRead)
def trace_resource(
    conversation_id: Identifier,
    run_id: Identifier,
    block_id: Identifier,
    resource_id: Identifier,
    service: Traces,
):
    return service.resource(conversation_id, run_id, block_id, resource_id)
