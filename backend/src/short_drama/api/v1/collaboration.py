from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.identity import require_actor
from short_drama.domain.collaboration import AuditEvent, UserModelPreference
from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.identity import InvitationCreate, PreferenceUpdate, Proof
from short_drama.service.collaboration_service import CollaborationService
from short_drama.service.model_preference_service import save_model_preference

router = APIRouter(tags=["Project collaboration"])


def service(request: Request):
    return CollaborationService(
        request.app.state.session_factory,
        request.app.state.settings,
        getattr(request.state, "actor", None),
    )


@router.get("/projects/{project_id}/members")
def members(project_id: Identifier, svc=Depends(service)):
    return svc.members(project_id)


@router.delete("/projects/{project_id}/members/{user_id}")
def remove(project_id: Identifier, user_id: Identifier, svc=Depends(service)):
    return svc.remove(project_id, user_id)


@router.post("/projects/{project_id}/leave")
def leave(project_id: Identifier, request: Request, svc=Depends(service)):
    return svc.remove(project_id, request.state.actor.user_id, leave=True)


@router.get("/projects/{project_id}/invitations")
def invitations(project_id: Identifier, svc=Depends(service)):
    return svc.invitations(project_id)


@router.post("/projects/{project_id}/invitations", status_code=201)
def invite(project_id: Identifier, payload: InvitationCreate, svc=Depends(service)):
    return svc.create_invitation(project_id, payload)


@router.delete("/projects/{project_id}/invitations/{invitation_id}")
def revoke(project_id: Identifier, invitation_id: Identifier, svc=Depends(service)):
    return svc.revoke(project_id, invitation_id)


@router.get("/invitations/{token}")
def preview(token: str, svc=Depends(service)):
    return svc.preview(token)


@router.post("/invitations/{token}/verification")
def request_proof(token: str, svc=Depends(service)):
    return svc.request_proof(token)


@router.post("/invitations/{token}/accept")
def accept(token: str, payload: Proof, svc=Depends(service)):
    return svc.accept(token, payload)


@router.get("/projects/{project_id}/activity")
def activity(project_id: Identifier, session: Session = Depends(get_session)):
    from short_drama.db.access import require_project

    with session.begin():
        require_project(session, project_id)
        rows = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.project_id == project_id)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(100)
        )
        return {
            "items": [
                {
                    "id": str(r.id),
                    "actor_user_id": str(r.actor_user_id),
                    "object_type": r.object_type,
                    "object_id": r.object_id,
                    "action": r.action,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        }


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
    source_type: Literal["asset", "media"]
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
