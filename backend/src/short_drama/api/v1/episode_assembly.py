from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.api.v1.episode_storyboard import ScopedId
from short_drama.core.exceptions import NotFound
from short_drama.domain import MediaFile
from short_drama.schemas.episode_assembly import (
    AssemblyApply,
    AssemblyEdit,
    AssemblyExport,
    AssemblyVersion,
)
from short_drama.service.episode_assembly_service import EpisodeAssemblyService
from short_drama.service.storage_service import StorageService

router = APIRouter(
    prefix="/projects/{project_id}/episodes/{episode_id}/assembly", tags=["Episode assembly"]
)


def service(request: Request, session: Session = Depends(get_session)):
    return EpisodeAssemblyService(session, request.app.state.settings, request.app.state.storage)


Service = Annotated[EpisodeAssemblyService, Depends(service)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.get("")
def get(project_id: ScopedId, episode_id: ScopedId, svc: Service):
    return svc.get(project_id, episode_id)


@router.post("/initialize")
def initialize(project_id: ScopedId, episode_id: ScopedId, svc: Service):
    return svc.initialize(project_id, episode_id)


@router.patch("")
def edit(project_id: ScopedId, episode_id: ScopedId, payload: AssemblyEdit, svc: Service):
    return svc.edit(project_id, episode_id, payload)


@router.post("/sync")
def sync(project_id: ScopedId, episode_id: ScopedId, payload: AssemblyVersion, svc: Service):
    return svc.sync(project_id, episode_id, payload)


@router.post("/exports", status_code=202)
def export(
    project_id: ScopedId,
    episode_id: ScopedId,
    payload: AssemblyExport,
    svc: Service,
    idempotency_key: Key,
):
    return svc.export(project_id, episode_id, payload, idempotency_key)


@router.get("/exports")
def history(
    project_id: ScopedId,
    episode_id: ScopedId,
    svc: Service,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return svc.jobs(project_id, episode_id, offset, limit)


@router.get("/exports/{job_id}")
def job(project_id: ScopedId, episode_id: ScopedId, job_id: ScopedId, svc: Service):
    return svc.job_action(project_id, episode_id, job_id, "get")


@router.post("/exports/{job_id}/cancel")
def cancel(project_id: ScopedId, episode_id: ScopedId, job_id: ScopedId, svc: Service):
    return svc.job_action(project_id, episode_id, job_id, "cancel")


@router.post("/exports/{job_id}/retry", status_code=202)
def retry(
    project_id: ScopedId, episode_id: ScopedId, job_id: ScopedId, svc: Service, idempotency_key: Key
):
    return svc.job_action(project_id, episode_id, job_id, "retry", key=idempotency_key)


@router.post("/exports/{job_id}/apply")
def apply(
    project_id: ScopedId,
    episode_id: ScopedId,
    job_id: ScopedId,
    payload: AssemblyApply,
    svc: Service,
):
    return svc.job_action(project_id, episode_id, job_id, "apply", payload)


@router.get("/exports/{job_id}/download")
def download(project_id: ScopedId, episode_id: ScopedId, job_id: ScopedId, svc: Service):
    result = svc.job_action(project_id, episode_id, job_id, "get")
    if result["status"] != "succeeded" or not result["media_id"]:
        raise NotFound("成片尚未完成")
    with svc._transaction():
        media = svc.session.get(MediaFile, int(result["media_id"]))
        locator = media.storage_locator
    storage = StorageService(svc.storage, svc.settings)

    def chunks():
        with storage.open(locator) as response:
            yield from response.stream(1024 * 1024)

    return StreamingResponse(
        chunks(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="episode-{episode_id}-{job_id}.mp4"'
        },
    )
