from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.api.v1.episode_storyboard import ScopedId
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.episode_sound import SoundAdopt, SoundEdit, VoiceDefaultsEdit
from short_drama.service.episode_sound_service import EpisodeSoundService
from short_drama.service.subtitles import export_srt, parse_srt

router = APIRouter(
    prefix="/projects/{project_id}/episodes/{episode_id}/sound", tags=["Episode sound"]
)


def service(request: Request, session: Session = Depends(get_session)):
    return EpisodeSoundService(session, request.app.state.settings, request.app.state.storage)


Service = Annotated[EpisodeSoundService, Depends(service)]


@router.get("/capabilities")
def capabilities(request: Request):
    return {"enabled": request.app.state.settings.audio_production_enabled}


@router.get("")
def get(project_id: ScopedId, episode_id: ScopedId, svc: Service):
    return svc.get(project_id, episode_id)


@router.put("")
def save(project_id: ScopedId, episode_id: ScopedId, body: SoundEdit, svc: Service):
    return svc.save(project_id, episode_id, body)


@router.put("/voices")
def voices(project_id: ScopedId, episode_id: ScopedId, body: VoiceDefaultsEdit, svc: Service):
    return svc.voices(project_id, episode_id, body)


@router.post("/music")
def upload(project_id: ScopedId, episode_id: ScopedId, svc: Service, file: UploadFile = File(...)):
    return svc.upload(project_id, episode_id, file.file, file.filename)


@router.get("/candidates/{line_id}")
def candidates(project_id: ScopedId, episode_id: ScopedId, line_id: str, svc: Service):
    return svc.candidates(project_id, episode_id, line_id)


@router.post("/adopt")
def adopt(project_id: ScopedId, episode_id: ScopedId, body: SoundAdopt, svc: Service):
    return svc.adopt(project_id, episode_id, body)


@router.get("/extractions/{task_id}")
def extraction(project_id: ScopedId, episode_id: ScopedId, task_id: ScopedId, svc: Service):
    return svc.extraction(project_id, episode_id, task_id)


@router.post("/subtitles/import")
def import_subtitles(
    project_id: ScopedId,
    episode_id: ScopedId,
    svc: Service,
    text: Annotated[str, Body(max_length=2097152, embed=True)],
):
    svc.get(project_id, episode_id)
    try:
        return {"subtitles": [s.model_dump() for s in parse_srt(text)]}
    except ValueError as error:
        raise WorkflowError("subtitle_invalid", str(error), 422) from None


@router.get("/subtitles.srt")
def subtitles(project_id: ScopedId, episode_id: ScopedId, svc: Service):
    doc = svc.get(project_id, episode_id)["document"]
    return Response(
        export_srt(doc["subtitles"]),
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'},
    )


@router.get("/native-subtitles")
def native_subtitles(project_id: ScopedId, episode_id: ScopedId, svc: Service):
    return svc.native_subtitles(project_id, episode_id)
