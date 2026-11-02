"""Every MCP HTTP request proves its bearer and resolves the connection peer once."""

from collections.abc import Awaitable, Callable
from ipaddress import ip_address, ip_network
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.service.identity.accounts.mcp_token import McpPrincipal, McpTokenService


class McpBearerAuth:
    def __init__(
        self, app: ASGIApp, *, authenticate: Callable[[Request], Awaitable[McpPrincipal]]
    ) -> None:
        self.app = app
        self.authenticate = authenticate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not (
            scope["path"] == "/mcp" or scope["path"].startswith("/mcp/")
        ):
            return await self.app(scope, receive, send)
        try:
            principal = await self.authenticate(Request(scope))
        except BusinessError as error:
            headers = {"Cache-Control": "no-store"}
            if error.status_code == 401:
                headers["WWW-Authenticate"] = "Bearer"
            response = JSONResponse(
                {"code": error.code, "message": error.message},
                status_code=error.status_code,
                headers=headers,
            )
            return await response(scope, receive, send)
        scope = {**scope, "state": {**scope.get("state", {}), "mcp_principal": principal}}
        await self.app(scope, receive, send)


def bearer_secret(request: Request) -> str:
    headers = request.headers.getlist("authorization")
    parts = headers[0].split() if len(headers) == 1 else []
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].startswith("sdmcp_"):
        raise WorkflowError("mcp_authentication_required", "需要有效的 MCP Bearer 令牌", 401)
    return parts[1]


def client_ip(request: Request, trusted_proxy_cidrs: list[str]) -> str:
    try:
        peer = ip_address(request.client.host if request.client else "")
        trusted = [ip_network(value) for value in trusted_proxy_cidrs]
        if not any(peer in network for network in trusted):
            return str(peer)
        forwarded = request.headers.getlist("x-forwarded-for")
        if not forwarded:
            return str(peer)
        chain = [ip_address(value.strip()) for header in forwarded for value in header.split(",")]
    except ValueError:
        raise WorkflowError("mcp_client_ip_invalid", "代理来源 IP 链无效", 400) from None
    for address in reversed([*chain, peer]):
        if not any(address in network for network in trusted):
            return str(address)
    return str(chain[0])


async def authenticate(request: Request) -> McpPrincipal:
    secret = bearer_secret(request)
    address = client_ip(request, request.app.state.settings.mcp_trusted_proxy_cidrs)
    request.state.mcp_client_ip = address
    request_id = uuid4().hex
    factory = request.app.state.session_factory

    def verify() -> McpPrincipal:
        with factory() as session:
            return McpTokenService(session, tool_specs=()).authenticate(secret, address, request_id)

    return await run_in_threadpool(verify)
