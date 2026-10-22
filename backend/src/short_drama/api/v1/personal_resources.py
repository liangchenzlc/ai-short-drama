from typing import Literal

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.identity import require_actor
from short_drama.domain.collaboration import UserModelPreference
from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.identity import PreferenceUpdate
from short_drama.service.model_preference_service import save_model_preference

router = APIRouter(tags=["Personal resources"])


@router.get("/users/me/model-preferences")
def preferences(session: Session = Depends(get_session)):
    actor = require_actor(session)
    with session.begin():
        return {
            "items": [
                {"context_key": p.context_key, "config_id": str(p.config_id)}
                for p in session.scalars(
                    select(UserModelPreference).where(UserModelPreference.user_id == actor.user_id)
                )
            ]
        }


@router.put("/users/me/model-preferences")
def save_preference(payload: PreferenceUpdate, session: Session = Depends(get_session)):
    require_actor(session)
    with session.begin():
        save_model_preference(session, payload.context_key, payload.config_id)
    return {"saved": True}


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
    from short_drama.service.resource_import_service import ResourceImportService

    return ResourceImportService(session).create(
        project_id, payload.source_type, payload.source_id, idempotency_key
    )


@router.get("/projects/{project_id}/imports/{import_id}")
def get_import(
    project_id: Identifier, import_id: Identifier, session: Session = Depends(get_session)
):
    from short_drama.service.resource_import_service import ResourceImportService

    return ResourceImportService(session).detail(project_id, import_id)
