import hmac
import re
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.core.identity import token_hash
from short_drama.db.access import scope_of
from short_drama.service.auth_service import AuthService

PUBLIC = {
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/verification/request",
    "/api/v1/auth/verification/confirm",
    "/api/v1/auth/password/request",
    "/api/v1/auth/password/reset",
    "/api/v1/test",
    "/api/v1/auth/capabilities",
}


def resource_scope(factory, actor, candidate):
    with factory() as session:
        session.info["actor"] = actor
        row = session.scalar(select(candidate[0]).where(candidate[0].id == candidate[1]))
        if row is None:
            raise WorkflowError("not_found", "Resource does not exist", 404)
        return scope_of(session, row)


def install_identity(app, settings):
    @app.middleware("http")
    async def identity(request, call_next):
        if not settings.auth_enabled or not request.url.path.startswith("/api/v1"):
            return await call_next(request)
        try:
            request.state.request_id = uuid4().hex
            request.state.actor = None
            request.state.resource_scope = None
            path = request.url.path.rstrip("/")
            anonymous_invite = (
                bool(re.fullmatch(r"/api/v1/invitations/[A-Za-z0-9_-]{43}", path))
                and request.method == "GET"
            )
            public = path in PUBLIC or anonymous_invite
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.headers.get("origin") != settings.public_origin.rstrip("/"):
                    raise WorkflowError("csrf_failed", "Request origin is invalid", 403)
            if not public or request.cookies.get("sd_session"):
                try:
                    actor = await run_in_threadpool(
                        AuthService(app.state.session_factory, settings).authenticate,
                        request.cookies.get("sd_session"),
                        request.state.request_id,
                    )
                except BusinessError:
                    if not public:
                        raise
                else:
                    request.state.actor = actor
                    if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(
                        actor.csrf_hash, token_hash(request.headers.get("x-csrf-token", ""))
                    ):
                        raise WorkflowError("csrf_failed", "Please reload and try again", 403)
                    if not actor.verified:
                        raise WorkflowError(
                            "email_verification_required", "Verify your email first", 403
                        )
                    from short_drama.domain import (
                        Asset,
                        AsyncTask,
                        GenerationBatchJob,
                        Project,
                        ShotScript,
                    )

                    candidate = None
                    for expression, model in [
                        (r"/projects/(\d+)", Project),
                        (r"/assets/(\d+)", Asset),
                        (r"/ai/generations/(\d+)", AsyncTask),
                        (r"/ai/generation-batches/(\d+)", GenerationBatchJob),
                        (r"/generation-references/asset/(\d+)", Asset),
                        (r"/generation-references/shot/(\d+)", ShotScript),
                    ]:
                        match = re.search(expression, path)
                        if match:
                            candidate = (model, int(match.group(1)))
                            break
                    if candidate:
                        request.state.resource_scope = await run_in_threadpool(
                            resource_scope,
                            app.state.session_factory,
                            actor,
                            candidate,
                        )
            response = await call_next(request)
            if response.status_code == 401:
                response.delete_cookie("sd_session", path="/")
                response.delete_cookie("sd_csrf", path="/")
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        except BusinessError as error:
            return JSONResponse(
                {"error": {"code": error.code, "message": error.message}},
                status_code=error.status_code,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
