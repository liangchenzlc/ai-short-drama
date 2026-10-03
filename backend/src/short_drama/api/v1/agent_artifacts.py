from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.agent_artifacts import (
    ArtifactAdopt,
    ArtifactKind,
    ArtifactRead,
    ArtifactStatus,
    ArtifactSummary,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.service.agent_artifact_service import AgentArtifactService

router = APIRouter(
    prefix="/projects/{project_id}/episodes/{episode_id}/agent-artifacts", tags=["Agent artifacts"]
)
ScopedId = Annotated[Identifier, Path(description="Decimal Snowflake identifier")]


def get_artifact_service(request: Request, session: Annotated[Session, Depends(get_session)]):
    if not getattr(request.app.state, "agent_schema_ready", False):
        raise WorkflowError("agent_schema_unavailable", "创作成果暂不可用，请联系管理员", 503)
    return AgentArtifactService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


Artifacts = Annotated[AgentArtifactService, Depends(get_artifact_service)]


@router.get("", response_model=PageResponse[ArtifactSummary])
def list_artifacts(
    project_id: ScopedId,
    episode_id: ScopedId,
    service: Artifacts,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    kind: ArtifactKind | None = None,
    status: ArtifactStatus | None = None,
):
    return service.list(project_id, episode_id, offset, limit, kind=kind, status=status)


@router.get("/{artifact_id}", response_model=ArtifactRead)
def get_artifact(
    project_id: ScopedId, episode_id: ScopedId, artifact_id: ScopedId, service: Artifacts
):
    return service.get(project_id, episode_id, artifact_id)


@router.post("/{artifact_id}/adopt", response_model=ArtifactRead)
def adopt_artifact(
    project_id: ScopedId,
    episode_id: ScopedId,
    artifact_id: ScopedId,
    payload: ArtifactAdopt,
    service: Artifacts,
):
    return service.adopt(project_id, episode_id, artifact_id, payload)
