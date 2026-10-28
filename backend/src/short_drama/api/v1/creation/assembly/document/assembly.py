from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.api.v1.creation.storyboard.management.storyboard import ScopedId
from short_drama.schemas.creation.assembly.document.assembly import (
    AssemblyApply,
    AssemblyExport,
    AssemblyVersion,
)
from short_drama.schemas.creation.assembly.editing.editor import (
    AssemblyEditorEdit,
    AudioAnalysisCreate,
    MaterialImport,
    TranscriptionCreate,
)
from short_drama.service.creation.assembly.document.assembly import ProjectAssemblyService
from short_drama.service.creation.assembly.editing.editor import AssemblyEditorService
from short_drama.service.creation.assembly.materials.media import AssemblyMediaService
from short_drama.service.media.files.storage import StorageService

router = APIRouter(prefix="/projects/{project_id}/assembly", tags=["Project assembly"])


def service(request: Request, session: Session = Depends(get_session)):
    return ProjectAssemblyService(session, request.app.state.settings, request.app.state.storage)


Service = Annotated[ProjectAssemblyService, Depends(service)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def editor(svc):
    return AssemblyEditorService(svc.session, svc.settings, svc.storage)


def media_service(svc):
    return AssemblyMediaService(svc.session, svc.settings, svc.storage)


@router.get("/editor")
def get_editor(
    project_id: ScopedId,
    svc: Service,
    material_ids: Annotated[
        str, Query(pattern=r"^([0-9]{1,20})(,[0-9]{1,20}){0,899}$|^$", max_length=19000)
    ] = "",
):
    return editor(svc).get_editor(
        project_id, material_ids=material_ids.split(",") if material_ids else []
    )


@router.put("/editor")
def save_editor(project_id: ScopedId, body: AssemblyEditorEdit, svc: Service):
    return editor(svc).save_editor(project_id, body)


@router.get("/materials")
def materials(project_id: ScopedId, svc: Service):
    return editor(svc).materials(project_id)


@router.post("/materials/import")
def import_material(project_id: ScopedId, body: MaterialImport, svc: Service):
    return editor(svc).import_material(project_id, body.media_id)


@router.post("/materials/upload")
def upload_material(project_id: ScopedId, svc: Service, file: UploadFile = File(...)):
    return editor(svc).upload_material(project_id, file.file, file.filename, file.content_type)


@router.get("/transcription-models")
def transcription_models(project_id: ScopedId, svc: Service):
    return media_service(svc).transcription_models(project_id)


@router.post("/transcriptions", status_code=202)
def transcribe(project_id: ScopedId, body: TranscriptionCreate, svc: Service, key: Key):
    return media_service(svc).create_media_job(project_id, body, key, "transcription")


@router.post("/audio-analysis", status_code=202)
def analyse(project_id: ScopedId, body: AudioAnalysisCreate, svc: Service, key: Key):
    return media_service(svc).create_media_job(project_id, body, key, "audio_analysis")


@router.get("/media-jobs")
def media_jobs(project_id: ScopedId, svc: Service):
    return media_service(svc).media_jobs(project_id)


@router.get("/media-jobs/{job_id}")
def media_job(project_id: ScopedId, job_id: ScopedId, svc: Service):
    return media_service(svc).media_job(project_id, job_id)


@router.post("/media-jobs/{job_id}/cancel")
def cancel_media_job(project_id: ScopedId, job_id: ScopedId, svc: Service):
    return media_service(svc).media_job(project_id, job_id, "cancel")


@router.post("/media-jobs/{job_id}/acknowledge")
def acknowledge_media_job(project_id: ScopedId, job_id: ScopedId, svc: Service):
    return media_service(svc).media_job(project_id, job_id, "acknowledge")


@router.get("")
def get(project_id: ScopedId, svc: Service):
    return svc.get(project_id)


@router.get("/current/download")
def download_current(project_id: ScopedId, svc: Service):
    media = svc.current_media(project_id)
    storage = StorageService(svc.storage, svc.settings)

    def chunks():
        with storage.open(media.storage_locator) as response:
            yield from response.stream(1024 * 1024)

    return StreamingResponse(
        chunks(),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="creation-{project_id}.mp4"'},
    )


@router.post("/initialize")
def initialize(project_id: ScopedId, svc: Service):
    return svc.initialize(project_id)


@router.post("/sync")
def sync(project_id: ScopedId, payload: AssemblyVersion, svc: Service):
    return editor(svc).sync(project_id, payload)


@router.post("/exports", status_code=202)
def export(
    project_id: ScopedId,
    payload: AssemblyExport,
    svc: Service,
    idempotency_key: Key,
):
    return svc.export(project_id, payload, idempotency_key)


@router.get("/exports")
def history(
    project_id: ScopedId,
    svc: Service,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return svc.jobs(project_id, offset, limit)


@router.post("/previews", status_code=202)
def preview(
    project_id: ScopedId,
    payload: AssemblyExport,
    svc: Service,
    idempotency_key: Key,
):
    return svc.export(project_id, payload, idempotency_key, kind="preview")


@router.get("/exports/{job_id}")
def job(project_id: ScopedId, job_id: ScopedId, svc: Service):
    return svc.job_action(project_id, job_id, "get")


@router.post("/exports/{job_id}/cancel")
def cancel(project_id: ScopedId, job_id: ScopedId, svc: Service):
    return svc.job_action(project_id, job_id, "cancel")


@router.post("/exports/{job_id}/retry", status_code=202)
def retry(project_id: ScopedId, job_id: ScopedId, svc: Service, idempotency_key: Key):
    return svc.job_action(project_id, job_id, "retry", key=idempotency_key)


@router.post("/exports/{job_id}/apply")
def apply(
    project_id: ScopedId,
    job_id: ScopedId,
    payload: AssemblyApply,
    svc: Service,
):
    return svc.job_action(project_id, job_id, "apply", payload)


@router.get("/exports/{job_id}/download")
def download(project_id: ScopedId, job_id: ScopedId, svc: Service):
    locator = svc.export_locator(project_id, job_id)
    storage = StorageService(svc.storage, svc.settings)

    def chunks():
        with storage.open(locator) as response:
            yield from response.stream(1024 * 1024)

    return StreamingResponse(
        chunks(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="creation-{project_id}-{job_id}.mp4"'
        },
    )
