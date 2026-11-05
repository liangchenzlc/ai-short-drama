"""Public FastMCP registration and lifespan, mounted only behind parent bearer auth."""

from fastmcp import FastMCP
from fastmcp.server.http import StarletteWithLifespan
from starlette.datastructures import State

from short_drama.api.dependencies.identity import get_mcp_tool_specs
from short_drama.api.mcp.tools import ScopedTools, catalog_tool
from short_drama.core.branding import get_brand


def create_mcp_app(state: State) -> StarletteWithLifespan:
    server = FastMCP(get_brand().name, mask_error_details=True)
    for spec in get_mcp_tool_specs():
        server.add_tool(catalog_tool(spec, state))
    server.add_middleware(ScopedTools())
    return server.http_app(path="/", stateless_http=False, json_response=True)
