from fastapi import APIRouter, Request

from short_drama.schemas.assistant.conversations.engine import AgentStatus

router = APIRouter()


@router.get("/agent/status", response_model=AgentStatus)
@router.get("/assistant/status", response_model=AgentStatus)
def agent_status(request: Request):
    return AgentStatus(
        enabled=request.app.state.settings.agent_enabled,
        schema_ready=getattr(request.app.state, "agent_schema_ready", False),
    )
