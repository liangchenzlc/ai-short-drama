"""Thin protocol adapters use the same catalog, schemas, handlers and business receipts."""

import base64
import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from fastapi.concurrency import run_in_threadpool
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import FunctionTool, Tool, ToolResult
from mcp.types import CallToolRequestParams, ImageContent, ListToolsRequest
from pydantic import ValidationError
from starlette.datastructures import State

from short_drama.agent.tools.contracts import ToolSpec
from short_drama.api.mcp.auth import bearer_secret
from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.core.identity import token_hash
from short_drama.schemas.identity.accounts.mcp_token import McpWriteRequest
from short_drama.service.assistant.operations.image import read_business_image_bytes
from short_drama.service.assistant.operations.invocation import (
    BusinessInvocation,
    BusinessInvocationService,
    InvocationScope,
)
from short_drama.service.identity.accounts.mcp_token import McpTokenService


def tool_error(error: BusinessError) -> ToolError:
    return ToolError(
        json.dumps(
            {"code": error.code, "message": error.message, "status_code": error.status_code},
            ensure_ascii=False,
        )
    )


class ScopedTools(Middleware):
    async def on_list_tools(
        self,
        context: MiddlewareContext[ListToolsRequest],
        call_next: CallNext[ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        principal = get_http_request().state.mcp_principal
        return [tool for tool in await call_next(context) if tool.name in principal.tool_names]

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        principal = get_http_request().state.mcp_principal
        if context.message.name not in principal.tool_names:
            raise tool_error(WorkflowError("business_tool_scope", "工具超出本令牌范围", 403))
        return await call_next(context)


def catalog_tool(spec: ToolSpec, state: State) -> FunctionTool:
    mutating = spec.effect in {"write", "submit"}
    schema = McpWriteRequest[spec.args_schema] if mutating else spec.args_schema

    async def invoke(**arguments: Any) -> dict[str, Any] | ToolResult:
        request = get_http_request()
        try:
            parsed = schema.model_validate(arguments)
        except ValidationError:
            raise ToolError(
                json.dumps(
                    {
                        "code": "validation_error",
                        "status_code": 422,
                        "message": "工具参数不符合声明的输入结构",
                    }
                )
            ) from None
        values = parsed.arguments if mutating else parsed
        principal = request.state.mcp_principal
        external_id = parsed.request_id if mutating else principal.actor.request_id

        def execute() -> dict[str, Any] | ToolResult:
            # 认证事务在业务事务前结束；撤销在每次协议请求和业务调用边界生效。
            with state.session_factory() as session:
                tokens = McpTokenService(session, tool_specs=())
                current = tokens.authenticate(
                    bearer_secret(request), request.state.mcp_client_ip, principal.actor.request_id
                )
                project_ids = tokens.current_project_ids(current.actor.user_id)
            identity = BusinessInvocation(
                actor=replace(current.actor, request_id=external_id),
                source="mcp",
                request_id=token_hash("mcp:" + external_id),
                token_id=current.token_id,
                scope=InvocationScope(
                    project_ids=project_ids,
                    tool_names=current.tool_names,
                    subject_policy="project",
                    allowed_subjects={},
                ),
            )
            if spec.name == "workbench__read_image_content":
                data, mime = read_business_image_bytes(
                    state.session_factory, identity, values, state.storage
                )
                return ToolResult(
                    content=[
                        ImageContent(
                            type="image", mime_type=mime, data=base64.b64encode(data).decode()
                        )
                    ]
                )
            with state.session_factory() as session:
                return BusinessInvocationService(session, state.settings).execute(
                    identity, spec.name, values, spec.handler, mutating=mutating
                )

        try:
            return await run_in_threadpool(execute)
        except BusinessError as error:
            raise tool_error(error) from None

    return FunctionTool(
        name=spec.name,
        description=spec.description,
        parameters=schema.model_json_schema(mode="serialization"),
        fn=invoke,
    )
