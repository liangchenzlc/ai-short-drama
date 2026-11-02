"""Account services receive the authoritative business tool directory by injection."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.registry import TOOL_SPECS
from short_drama.api.dependencies.session import get_session
from short_drama.service.identity.accounts.mcp_token import McpTokenService


def get_mcp_tool_specs() -> tuple[ToolSpec, ...]:
    return tuple(spec for spec in TOOL_SPECS if spec.source == "business" and spec.expose_mcp)


def get_mcp_token_service(
    session: Annotated[Session, Depends(get_session)],
    tools: Annotated[tuple[ToolSpec, ...], Depends(get_mcp_tool_specs)],
) -> McpTokenService:
    return McpTokenService(session, tool_specs=tools)


McpTokens = Annotated[McpTokenService, Depends(get_mcp_token_service)]
