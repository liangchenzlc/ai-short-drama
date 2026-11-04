"""Named bearer lifecycle, account ownership and current authentication."""

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_address, ip_network

from sqlalchemy.orm import Session

from short_drama.agent.tools.contracts import ToolSpec
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
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
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id

MCP_READ_TOOLS = (
    "workbench__list_projects",
    "workbench__read_project",
    "workbench__read_assets",
    "workbench__read_storyboard",
    "workbench__read_current_script",
    "workbench__read_image_group",
    "workbench__read_image_content",
    "workbench__get_generation_status",
)
MCP_WORKBENCH_TOOLS = (
    "workbench__apply_standard_patch",
    "workbench__apply_canvas_patch",
    "workbench__set_image_group_primary",
)
MCP_MEDIA_TOOLS = ("workbench__submit_generation_batch",)
MCP_CAPABILITY_GROUPS = {
    "read": MCP_READ_TOOLS,
    "workbench": MCP_WORKBENCH_TOOLS,
    "media": MCP_MEDIA_TOOLS,
}


def granted_tools(values: McpTokenCreate) -> list[str]:
    names = list(MCP_READ_TOOLS)
    if values.workbench_enabled:
        names.extend(MCP_WORKBENCH_TOOLS)
        if values.media_generation_enabled:
            names.extend(MCP_MEDIA_TOOLS)
    return names


@dataclass(frozen=True)
class McpPrincipal:
    actor: ActorContext
    token_id: int
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

    def create(self, values: McpTokenCreate, idempotency_key: str) -> McpTokenCreated:
        actor = require_actor(self.session)
        intent = values.model_dump(mode="json", exclude={"expires_at"})
        intent["media_generation_enabled"] = (
            values.workbench_enabled and values.media_generation_enabled
        )
        # 默认有效期描述的是创建意图；只在首次受理时换算，重放不延长有效期。
        intent["expires_at"] = (
            (values.expires_at.isoformat() if values.expires_at else None)
            if "expires_at" in values.model_fields_set
            else "90_days"
        )
        request_hash = digest(intent)
        with self._transaction():
            self.tokens.lock_owner(actor.user_id)
            existing = self.tokens.by_create_key(actor.user_id, idempotency_key)
            if existing is not None:
                if existing.create_hash != request_hash:
                    raise Conflict("相同创建标识不能更改令牌参数")
                return McpTokenCreated(
                    **McpTokenRead.model_validate(existing).model_dump(), secret=None
                )
            secret, token_digest, hint = issue_mcp_secret()
            row = McpAccessToken(
                id=next_id(),
                owner_user_id=actor.user_id,
                name=values.name,
                token_hash=token_digest,
                token_hint=hint,
                create_key=idempotency_key,
                create_hash=request_hash,
                tool_names=granted_tools(values),
                expires_at=(
                    values.expires_at
                    if "expires_at" in values.model_fields_set
                    else utcnow() + timedelta(days=90)
                ),
                allowed_ips=values.allowed_ips,
                created_at=utcnow(),
            )
            self.session.add(row)
            self.session.flush()
            return McpTokenCreated(**McpTokenRead.model_validate(row).model_dump(), secret=secret)

    def list(self, create_key: str | None = None) -> list[McpTokenRead]:
        actor = require_actor(self.session)
        with self._transaction(read_only=True):
            return [
                McpTokenRead.model_validate(row)
                for row in self.tokens.list(actor.user_id, create_key)
            ]

    def delete(self, identifier: int) -> None:
        actor = require_actor(self.session)
        with self._transaction():
            row = self.tokens.owned(actor.user_id, identifier)
            if row is None:
                raise NotFound("MCP 令牌不存在")
            if row.deleted_at is None:
                row.deleted_at = utcnow()
            self.session.flush()

    def current_project_ids(self, owner: int) -> frozenset[int]:
        with self._transaction(read_only=True):
            return frozenset(row.id for row in self.tokens.projects(owner))

    def options(self, public_origin: str) -> McpTokenOptions:
        require_actor(self.session)
        return McpTokenOptions(
            connection_url=public_origin.rstrip("/") + "/mcp/",
            capability_groups={key: list(names) for key, names in MCP_CAPABILITY_GROUPS.items()},
            tools=[
                {
                    "name": spec.name,
                    "description": spec.description,
                    "modes": sorted(spec.modes),
                    "mutating": spec.effect in {"write", "submit"},
                }
                for name in (*MCP_READ_TOOLS, *MCP_WORKBENCH_TOOLS, *MCP_MEDIA_TOOLS)
                for spec in (self.tools[name],)
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
                tool_names=frozenset(token.tool_names),
            )
