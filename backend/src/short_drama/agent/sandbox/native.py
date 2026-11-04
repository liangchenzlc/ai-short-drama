"""Retain actual upstream filesystem BaseTools, bound to the scoped facade."""

from deepagents.middleware.filesystem import FilesystemMiddleware

from short_drama.agent.sandbox.backend import ScopedBackend
from short_drama.agent.tools.contracts import ToolSpec

FILESYSTEM = FilesystemMiddleware(
    backend=ScopedBackend(),
    tools=["ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"],
    tool_token_limit_before_evict=None,
    offload_binary_content=False,
)


def native_spec(name, effect, description):
    tool = next(tool for tool in FILESYSTEM.tools if tool.name == name)
    schema = tool.tool_call_schema
    return ToolSpec(
        name=name,
        description=description,
        args_schema=schema if isinstance(schema, dict) else schema.model_json_schema(),
        handler=tool,
        effect=effect,
        source="runtime",
    )
