from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from short_drama.api.dependencies.ai_config import get_model_preference_service
from short_drama.api.dependencies.session import get_session
from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.identity.accounts.identity import PreferenceUpdate
from short_drama.service.ai_config.preferences.preferences import ModelPreferenceService

router = APIRouter(tags=["Personal resources"])


Preferences = Annotated[ModelPreferenceService, Depends(get_model_preference_service)]


@router.get("/users/me/model-preferences")
def preferences(service: Preferences):
    return service.list_preferences()


@router.put("/users/me/model-preferences")
def save_preference(payload: PreferenceUpdate, service: Preferences):
    return service.save_preference(payload.context_key, payload.config_id)


class ImportRequest(InputModel):
    source_type: Literal["media"]
    source_id: Identifier


@router.post("/projects/{project_id}/imports", status_code=202)
def create_import(
    project_id: Identifier,
    payload: ImportRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    session: Session = Depends(get_session),
):
    from short_drama.service.media.imports.resources import ResourceImportService

    return ResourceImportService(session).create(
        project_id, payload.source_type, payload.source_id, idempotency_key
    )


@router.get("/projects/{project_id}/imports/{import_id}")
def get_import(
    project_id: Identifier, import_id: Identifier, session: Session = Depends(get_session)
):
    from short_drama.service.media.imports.resources import ResourceImportService

    return ResourceImportService(session).detail(project_id, import_id)
