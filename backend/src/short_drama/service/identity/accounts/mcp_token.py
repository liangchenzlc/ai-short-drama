"""Named bearer lifecycle, account ownership and current authentication."""

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from sqlalchemy.orm import Session

from short_drama.agent.tools.contracts import ToolSpec
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext, require_actor, token_hash
from short_drama.core.time import utcnow
from short_drama.dao.identity.accounts.mcp_token import McpTokenDAO
from short_drama.domain.identity.accounts.mcp_token import McpAccessToken
from short_drama.schemas.identity.accounts.mcp_token import (
    McpTokenCreate,
    McpTokenCreated,
    McpTokenOptions,
    McpTokenRead,
)
from short_drama.service.base import BaseService
from short_drama.utils.snowflake import next_id


@dataclass(frozen=True)
class McpPrincipal:
    actor: ActorContext
    token_id: int
    project_ids: frozenset[int]
    tool_names: frozenset[str]


def issue_mcp_secret() -> tuple[str, str, str]:
    secret = "sdmcp_" + secrets.token_urlsafe(32)
    return secret, token_hash(secret), secret[:10] + "…" + secret[-4:]


class McpTokenService(BaseService):
    model = McpAccessToken

    def __init__(self, session: Session, *, tool_specs: Iterable[ToolSpec]) -> None:
        super().__init__(session)
        self.tokens = McpTokenDAO(session)
        self.tools = {
            spec.name: spec for spec in tool_specs if spec.source == "business" and spec.expose_mcp
        }

    def create(self, values: McpTokenCreate) -> McpTokenCreated:
        actor = require_actor(self.session)
        with self._transaction():
            owned_projects = {row.id for row in self.tokens.projects(actor.user_id)}
            if not set(values.project_ids).issubset(owned_projects):
                raise WorkflowError("mcp_project_scope", "只能授权本人当前拥有的作品", 403)
            if not set(values.tool_names).issubset(self.tools):
                raise WorkflowError("mcp_tool_scope", "只能授权可外放的业务工具", 403)
            secret, digest, hint = issue_mcp_secret()
            row = McpAccessToken(
                id=next_id(),
                owner_user_id=actor.user_id,
                name=values.name,
                token_hash=digest,
                token_hint=hint,
                project_ids=[str(value) for value in dict.fromkeys(values.project_ids)],
                tool_names=list(dict.fromkeys(values.tool_names)),
                expires_at=values.expires_at,
                allowed_ips=values.allowed_ips,
                created_at=utcnow(),
            )
            self.session.add(row)
            self.session.flush()
            return McpTokenCreated(**McpTokenRead.model_validate(row).model_dump(), secret=secret)

    def list(self) -> list[McpTokenRead]:
        actor = require_actor(self.session)
        with self._transaction(read_only=True):
            return [McpTokenRead.model_validate(row) for row in self.tokens.list(actor.user_id)]

    def delete(self, identifier: int) -> None:
        actor = require_actor(self.session)
        with self._transaction():
            row = self.tokens.owned(actor.user_id, identifier)
            if row is None:
                raise NotFound("MCP 令牌不存在")
            row.deleted_at = utcnow()
            self.session.flush()

    def options(self) -> McpTokenOptions:
        actor = require_actor(self.session)
        with self._transaction(read_only=True):
            return McpTokenOptions(
                projects=self.tokens.projects(actor.user_id),
                tools=[
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "modes": sorted(spec.modes),
                        "mutating": spec.effect in {"write", "submit"},
                    }
                    for spec in self.tools.values()
                ],
            )

    def authenticate(self, secret: str, client_ip: str, request_id: str) -> McpPrincipal:
        with self._transaction(read_only=True):
            row = self.tokens.by_hash(token_hash(secret))
            now = utcnow()
            if row is None:
                raise WorkflowError("mcp_authentication_required", "MCP 令牌无效或已撤销", 401)
            token, user = row
            if (
                token.deleted_at is not None
                or token.expires_at is not None
                and token.expires_at <= now
                or user.status != "active"
                or user.email_verified_at is None
            ):
                raise WorkflowError("mcp_authentication_required", "MCP 令牌无效或已撤销", 401)
            address = ip_address(client_ip)
            if token.allowed_ips and not any(
                address in ip_network(value) for value in token.allowed_ips
            ):
                raise WorkflowError("mcp_ip_forbidden", "当前来源 IP 未获授权", 403)
            return McpPrincipal(
                actor=ActorContext(user.id, user.username, user.email, True, request_id),
                token_id=token.id,
                project_ids=frozenset(int(value) for value in token.project_ids),
                tool_names=frozenset(token.tool_names),
            )
