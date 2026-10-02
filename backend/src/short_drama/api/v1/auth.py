from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import or_, select, update

from short_drama.core.exceptions import WorkflowError
from short_drama.domain.collaboration import User, UserSession
from short_drama.schemas.identity import EmailRequest, Login, PasswordReset, Proof, Registration
from short_drama.service.auth_service import AuthService, user_dto
from short_drama.service.base import utcnow

router = APIRouter(tags=["Accounts"])


def auth(request):
    return AuthService(request.app.state.session_factory, request.app.state.settings)


def limit(request, action, identifier, maximum=10):
    svc = auth(request)
    # Never trust X-Forwarded-For; proxy must provide a validated request.client.
    svc.limit(action + ":ip", request.client.host if request.client else "unknown", maximum * 4)
    svc.limit(action, identifier, maximum)
    return svc


@router.get("/auth/capabilities")
def capabilities(request: Request):
    return {"enabled": request.app.state.settings.auth_enabled}


@router.post("/auth/register", status_code=201)
def register(payload: Registration, request: Request):
    return limit(request, "register", payload.email, 5).register(payload)


@router.post("/auth/login")
def login(payload: Login, request: Request, response: Response):
    user, token, csrf = limit(request, "login", payload.username.lower(), 10).login(payload)
    settings = request.app.state.settings
    response.set_cookie(
        "sd_session",
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_days * 86400,
        path="/",
    )
    response.set_cookie(
        "sd_csrf",
        csrf,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_days * 86400,
        path="/",
    )
    return {"user": user}


@router.get("/auth/me")
def me(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise WorkflowError("authentication_required", "Please sign in", 401)
    with request.app.state.session_factory() as session:
        return {"user": user_dto(session.get(User, actor.user_id))}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    actor = getattr(request.state, "actor", None)
    if actor:
        with request.app.state.session_factory.begin() as session:
            session.execute(
                update(UserSession)
                .where(UserSession.id == actor.session_id)
                .values(revoked_at=utcnow())
            )
    response.delete_cookie("sd_session", path="/")
    response.delete_cookie("sd_csrf", path="/")
    return {"signed_out": True}


@router.post("/auth/verification/request")
def request_verification(payload: EmailRequest, request: Request):
    return limit(request, "registration_email", payload.email, 5).request_email(
        payload.email, "registration"
    )


@router.post("/auth/verification/confirm")
def verify(payload: Proof, request: Request):
    return limit(request, "registration_proof", str(payload.challenge_id), 10).verify(payload)


@router.post("/auth/password/request")
def request_password(payload: EmailRequest, request: Request):
    return limit(request, "password_email", payload.email, 5).request_email(
        payload.email, "password_reset"
    )


@router.post("/auth/password/reset")
def reset_password(payload: PasswordReset, request: Request):
    return limit(request, "password_proof", str(payload.challenge_id), 10).verify(
        payload, "password_reset"
    )


@router.get("/users/search")
def search_users(request: Request, q: Annotated[str, Query(min_length=2, max_length=254)]):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise WorkflowError("authentication_required", "Please sign in", 401)
    auth(request).limit("user_search", str(actor.user_id), 30, 60)
    value = q.strip().casefold()
    with request.app.state.session_factory() as session:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = session.scalars(
            select(User)
            .where(
                User.status == "active",
                User.email_verified_at.is_not(None),
                or_(
                    User.username == value,
                    User.email == value,
                    User.id == int(value) if value.isdecimal() and len(value) <= 20 else False,
                    User.display_name.like(escaped + "%", escape="\\"),
                ),
            )
            .order_by(User.username)
            .limit(5)
        )
        return {
            "items": [
                {"id": str(u.id), "username": u.username, "display_name": u.display_name}
                for u in rows
            ]
        }
