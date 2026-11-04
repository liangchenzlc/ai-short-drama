from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response

from short_drama.api.dependencies.identity import McpTokens
from short_drama.schemas.base import Identifier
from short_drama.schemas.identity.accounts.mcp_token import (
    McpTokenCreate,
    McpTokenCreated,
    McpTokenOptions,
    McpTokenRead,
)

router = APIRouter(prefix="/account/mcp-tokens", tags=["account-mcp-tokens"])


@router.get("/options", response_model=McpTokenOptions)
def options(request: Request, service: McpTokens) -> McpTokenOptions:
    return service.options(request.app.state.settings.public_origin)


@router.get("", response_model=list[McpTokenRead])
def list_tokens(
    service: McpTokens, create_key: Annotated[str | None, Query(max_length=64)] = None
) -> list[McpTokenRead]:
    return service.list(create_key)


@router.post("", response_model=McpTokenCreated, status_code=201)
def create_token(
    payload: McpTokenCreate,
    service: McpTokens,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$"),
    ],
) -> McpTokenCreated:
    result = service.create(payload, idempotency_key)
    if result.secret is None:
        response.status_code = 200
    return result


@router.delete("/{identifier}", status_code=204)
def delete_token(identifier: Identifier, service: McpTokens) -> Response:
    service.delete(identifier)
    return Response(status_code=204)
