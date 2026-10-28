from fastapi import APIRouter, Request, Response

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.identity.accounts.identity import (
    EmailRequest,
    Login,
    PasswordReset,
    Proof,
    Registration,
)
from short_drama.service.identity.authentication.auth import AuthService

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
    return {"user": auth(request).current_user(actor)}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    actor = getattr(request.state, "actor", None)
    auth(request).logout(actor)
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
