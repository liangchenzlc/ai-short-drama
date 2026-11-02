from fastapi import APIRouter

from short_drama.api.dependencies.assistant import Preferences
from short_drama.schemas.assistant.settings.preferences import (
    AssistantSettingsPatch,
    AssistantSettingsRead,
)

router = APIRouter()


@router.get("/settings", response_model=AssistantSettingsRead)
def assistant_settings(service: Preferences):
    return service.get_settings()


@router.patch("/settings", response_model=AssistantSettingsRead)
def update_assistant_settings(payload: AssistantSettingsPatch, service: Preferences):
    return service.update_settings(payload)
