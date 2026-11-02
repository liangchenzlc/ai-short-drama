from fastapi import APIRouter, Response

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
def options(service: McpTokens) -> McpTokenOptions:
    return service.options()


@router.get("", response_model=list[McpTokenRead])
def list_tokens(service: McpTokens) -> list[McpTokenRead]:
    return service.list()


@router.post("", response_model=McpTokenCreated, status_code=201)
def create_token(payload: McpTokenCreate, service: McpTokens) -> McpTokenCreated:
    return service.create(payload)


@router.delete("/{identifier}", status_code=204)
def delete_token(identifier: Identifier, service: McpTokens) -> Response:
    service.delete(identifier)
    return Response(status_code=204)
