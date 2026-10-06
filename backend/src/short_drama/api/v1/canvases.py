"""Canvas permissions and transactions remain in the service layer."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.base import Identifier
from short_drama.schemas.canvas import (
    CanvasArchiveRead,
    CanvasCommitRequest,
    CanvasCreationRead,
    CanvasListRead,
    CanvasRead,
    CanvasReceiptRead,
    CanvasRecycleListRead,
    CanvasRecyclePurgeRead,
    CanvasRecycleRestoreRequest,
    CanvasRecycleStatusRead,
    CanvasRestoreRequest,
    CanvasRevisionListRead,
    CanvasRevisionRead,
    CanvasSummaryRead,
    CanvasUserStateRead,
    CanvasUserStateRequest,
    CanvasVersionRequest,
    CanvasViewportRequest,
    CanvasViewPreferencesRead,
    CanvasViewPreferencesRequest,
    CanvasWorkspaceListRead,
)
from short_drama.schemas.canvas_creation import CanvasCreationRequest as CanvasCreateRequest
from short_drama.schemas.canvas_workspace import (
    CanvasWorkspaceModelsRead,
    CanvasWorkspacePreferencesRequest,
)
from short_drama.service.canvas_service import CanvasService
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService

router = APIRouter(tags=["Infinite canvases"])
ProjectId = Annotated[Identifier, Path()]
CanvasId = Annotated[Identifier, Path()]
RevisionId = Annotated[Identifier, Path()]
WriteKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def get_canvas_service(request: Request, session: Session = Depends(get_session)):
    expected_actor = request.headers.get("X-Canvas-Actor")
    actor = session.info.get("actor")
    if expected_actor is not None and (actor is None or expected_actor != str(actor.user_id)):
        raise WorkflowError("canvas_actor_changed", "The canvas account changed", 409)
    return CanvasService(session)


Canvases = Annotated[CanvasService, Depends(get_canvas_service)]
CANVAS = "/projects/{project_id}/canvases/{canvas_id}"


@router.get("/canvas-runtime/recycle-bin", response_model=CanvasRecycleListRead)
def list_recycled_canvases(service: Canvases):
    from short_drama.service.canvas_recycle_service import CanvasRecycleService

    return CanvasRecycleService(service.session).list_archived()


@router.post(
    "/canvas-runtime/canvas-projects/{source_key}/recycle-purge",
    response_model=CanvasRecyclePurgeRead,
)
def purge_recycled_canvas(
    source_key: Annotated[str, Path(min_length=1, max_length=64)],
    payload: CanvasRecycleRestoreRequest,
    key: WriteKey,
    service: Canvases,
):
    from short_drama.service.canvas_recycle_service import CanvasRecycleService

    return CanvasRecycleService(service.session).purge_archived(
        source_key, payload.archive_key, key
    )


@router.get("/canvas-runtime/workspace/model-config", response_model=CanvasWorkspaceModelsRead)
def workspace_model_config(service: Canvases):
    return CanvasWorkspaceService(service.session).read_models()


@router.put("/canvas-runtime/workspace/model-config", response_model=CanvasWorkspaceModelsRead)
def save_workspace_preferences(
    payload: CanvasWorkspacePreferencesRequest, request: Request, service: Canvases
):
    return CanvasWorkspaceService(service.session, request.app.state.settings).save_preferences(
        payload
    )


@router.get(
    "/canvas-workspace", response_model=CanvasWorkspaceListRead, response_model_exclude_none=True
)
def workspace(
    service: Canvases,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    q: Annotated[str, Query(max_length=255)] = "",
    project_id: Annotated[Identifier | None, Query()] = None,
    sort: Literal["updated", "created"] = "updated",
    include_documents: bool = False,
):
    return service.list_workspace(
        page=page,
        page_size=page_size,
        query=q,
        project_id=project_id,
        sort=sort,
        include_documents=include_documents,
    )


@router.get("/canvas-runtime/canvas-projects/{source_key}/events")
def canvas_revision_events(
    source_key: Annotated[str, Path(min_length=1, max_length=64)],
    actor_id: Annotated[Identifier, Query()],
    request: Request,
    service: Canvases,
):
    from short_drama.api.canvas_events import canvas_events

    if service.actor_id != actor_id:
        raise WorkflowError("canvas_actor_changed", "The canvas account changed", 409)
    service.resolve(source_key)
    return StreamingResponse(
        canvas_events(request, source_key, actor_id),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


def creation_service(service: CanvasService, request: Request):
    from short_drama.service.canvas_creation_service import CanvasCreationService

    return CanvasCreationService(
        service.session, request.app.state.settings, request.app.state.storage
    )


@router.post("/canvas-workspace", status_code=201, response_model=CanvasCreationRead)
def create_workspace_canvas(
    payload: CanvasCreateRequest, key: WriteKey, service: Canvases, request: Request
):
    return creation_service(service, request).create(payload, key)


@router.get("/canvas-workspace/resolve/{source_key}", response_model=CanvasSummaryRead)
def resolve_canvas(
    source_key: Annotated[str, Path(min_length=1, max_length=64)], service: Canvases
):
    return service.resolve(source_key)


@router.get("/canvas-write-receipts/{key}", response_model=CanvasReceiptRead)
def read_write_receipt(key: str, service: Canvases):
    return service.read_receipt(key)


@router.post(
    "/canvas-runtime/canvas-projects/{source_key}/recycle-restore", response_model=CanvasSummaryRead
)
def restore_recycled_canvas(
    source_key: Annotated[str, Path(min_length=1, max_length=64)],
    payload: CanvasRecycleRestoreRequest,
    key: WriteKey,
    service: Canvases,
):
    from short_drama.service.canvas_recycle_service import CanvasRecycleService

    return CanvasRecycleService(service.session).restore_archived(
        source_key, payload.archive_key, key
    )


@router.get(
    "/canvas-runtime/canvas-projects/{source_key}/recycle-status",
    response_model=CanvasRecycleStatusRead,
)
def read_archive_status(
    source_key: Annotated[str, Path(min_length=1, max_length=64)],
    archive_key: Annotated[str, Query(min_length=1, max_length=128)],
    service: Canvases,
):
    from short_drama.service.canvas_recycle_service import CanvasRecycleService

    return CanvasRecycleService(service.session).archive_status(source_key, archive_key)


@router.get("/projects/{project_id}/canvases", response_model=CanvasListRead)
def list_canvases(project_id: ProjectId, service: Canvases):
    return service.list_for_project(project_id)


@router.post("/projects/{project_id}/canvases", status_code=201, response_model=CanvasCreationRead)
def create_canvas(
    project_id: ProjectId,
    payload: CanvasCreateRequest,
    key: WriteKey,
    service: Canvases,
    request: Request,
):
    return creation_service(service, request).create(payload, key, project_id=project_id)


@router.get(CANVAS, response_model=CanvasRead)
def read_canvas(project_id: ProjectId, canvas_id: CanvasId, service: Canvases):
    return service.read(project_id, canvas_id)


@router.get(CANVAS + "/my-document", response_model=CanvasRead)
def read_my_canvas(project_id: ProjectId, canvas_id: CanvasId, service: Canvases):
    return service.read(project_id, canvas_id, private=True)


@router.post(CANVAS + "/commits", response_model=CanvasSummaryRead)
def commit_canvas(
    project_id: ProjectId,
    canvas_id: CanvasId,
    payload: CanvasCommitRequest,
    key: WriteKey,
    service: Canvases,
):
    return service.commit(project_id, canvas_id, payload, key)


@router.delete(CANVAS, response_model=CanvasArchiveRead)
def archive_canvas(
    project_id: ProjectId,
    canvas_id: CanvasId,
    payload: CanvasVersionRequest,
    key: WriteKey,
    service: Canvases,
):
    return service.archive(project_id, canvas_id, payload.expected_row_version, key)


@router.get(CANVAS + "/user-state", response_model=CanvasUserStateRead)
def read_user_state(project_id: ProjectId, canvas_id: CanvasId, service: Canvases):
    return service.read_user_state(project_id, canvas_id)


@router.patch(CANVAS + "/user-state", response_model=CanvasUserStateRead)
def update_user_state(
    project_id: ProjectId, canvas_id: CanvasId, payload: CanvasUserStateRequest, service: Canvases
):
    return service.update_user_state(project_id, canvas_id, payload)


@router.put(CANVAS + "/viewport", response_model=CanvasUserStateRead)
def update_viewport(
    project_id: ProjectId, canvas_id: CanvasId, payload: CanvasViewportRequest, service: Canvases
):
    return service.update_viewport(project_id, canvas_id, payload)


@router.get(CANVAS + "/revisions", response_model=CanvasRevisionListRead)
def list_revisions(project_id: ProjectId, canvas_id: CanvasId, service: Canvases):
    return service.list_revisions(project_id, canvas_id)


@router.put(CANVAS + "/view-preferences", response_model=CanvasViewPreferencesRead)
def update_view_preferences(
    project_id: ProjectId,
    canvas_id: CanvasId,
    payload: CanvasViewPreferencesRequest,
    service: Canvases,
):
    return service.update_view_preferences(project_id, canvas_id, payload)


@router.get(CANVAS + "/revisions/{revision_id}", response_model=CanvasRevisionRead)
def read_revision(
    project_id: ProjectId, canvas_id: CanvasId, revision_id: RevisionId, service: Canvases
):
    return service.read_revision(project_id, canvas_id, revision_id)


@router.post(CANVAS + "/revisions/{revision_id}/restore", response_model=CanvasSummaryRead)
def restore_revision(
    project_id: ProjectId,
    canvas_id: CanvasId,
    revision_id: RevisionId,
    payload: CanvasRestoreRequest,
    key: WriteKey,
    service: Canvases,
):
    heads = payload.model_dump(mode="json")["expected_drawing_heads"]
    return service.restore(
        project_id, canvas_id, revision_id, payload.expected_row_version, key, heads
    )
