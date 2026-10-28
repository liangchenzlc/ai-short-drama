from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.api.v1.creation.storyboard.management.storyboard import ScopedId
from short_drama.schemas.creation.sound.voices.voices import (
    NativeDialogueEdit,
    SoundModeEdit,
    VoiceAdopt,
)
from short_drama.service.creation.sound.voices.voices import NativeVoiceService

router = APIRouter(tags=["Native video voices"])


def service(request: Request, session: Session = Depends(get_session)):
    return NativeVoiceService(session, request.app.state.settings, request.app.state.storage)


Service = Annotated[NativeVoiceService, Depends(service)]


@router.get("/native-voice/capabilities")
def capabilities(request: Request):
    settings = request.app.state.settings
    return {
        "enabled": settings.native_video_enabled and settings.audio_production_enabled,
        "max_speakers": 2,
        "min_sample_ms": 3000,
        "max_sample_ms": 7500,
    }


@router.get("/projects/{project_id}/sound-mode")
def mode(project_id: ScopedId, svc: Service):
    return svc.mode(project_id)


@router.put("/projects/{project_id}/sound-mode")
def set_mode(project_id: ScopedId, body: SoundModeEdit, svc: Service):
    return svc.set_mode(project_id, body)


@router.get("/projects/{project_id}/characters/{asset_id}/voice")
def voices(project_id: ScopedId, asset_id: ScopedId, svc: Service):
    return svc.voices(project_id, asset_id)


@router.post("/projects/{project_id}/characters/{asset_id}/voice/adopt")
def adopt(project_id: ScopedId, asset_id: ScopedId, body: VoiceAdopt, svc: Service):
    return svc.adopt(project_id, asset_id, body)


@router.get("/projects/{project_id}/shots/{shot_id}/dialogue")
def dialogue(project_id: ScopedId, shot_id: ScopedId, svc: Service):
    return svc.dialogue(project_id, shot_id)


@router.put("/projects/{project_id}/shots/{shot_id}/dialogue")
def save_dialogue(
    project_id: ScopedId,
    shot_id: ScopedId,
    body: NativeDialogueEdit,
    svc: Service,
):
    return svc.save_dialogue(project_id, shot_id, body)
