"""Assistant preferences and MCP tokens are independent authority sources."""

from sqlalchemy.orm import Session

from short_drama.agent.tools.contracts import ToolCapability
from short_drama.core.exceptions import WorkflowError
from short_drama.core.time import utcnow
from short_drama.dao.assistant.settings.preferences import AssistantSettingsDAO
from short_drama.dao.identity.accounts.mcp_token import McpTokenDAO
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation


def require_receipt_capability_locked(
    session: Session, receipt: BusinessToolInvocation, capability: ToolCapability
) -> None:
    if receipt.source == "mcp":
        require_mcp_tool_locked(session, receipt.owner_user_id, receipt.token_id, receipt.tool_name)
    else:
        require_assistant_capability_locked(session, receipt.owner_user_id, capability)


def require_assistant_capability_locked(
    session: Session, owner: int, capability: ToolCapability
) -> None:
    if capability == "none":
        return
    row = AssistantSettingsDAO(session).preference(owner, lock=True)
    if row is None:
        return
    if capability in {"memory_read", "memory_write"}:
        if not row.long_term_memory_enabled:
            raise WorkflowError("assistant_memory_disabled", "长期记忆已关闭", 403)
        return
    if not row.workbench_enabled:
        raise WorkflowError("assistant_workbench_disabled", "工作台操作已关闭", 403)
    if capability == "media_generate" and not row.media_generation_enabled:
        raise WorkflowError("assistant_media_disabled", "媒体生成已关闭", 403)


def require_mcp_tool_locked(
    session: Session, owner: int, token_id: int, tool_name: str | None
) -> None:
    token = McpTokenDAO(session).owned(owner, token_id)
    if (
        token is None
        or token.deleted_at is not None
        or token.expires_at is not None
        and token.expires_at <= utcnow()
    ):
        raise WorkflowError("mcp_authentication_required", "MCP 令牌已过期或撤销", 403)
    if tool_name is not None and tool_name not in token.tool_names:
        raise WorkflowError("mcp_tool_scope", "MCP 令牌未授权此工具", 403)
