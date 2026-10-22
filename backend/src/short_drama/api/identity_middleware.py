import hmac
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.core.identity import token_hash
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
            public = path in PUBLIC
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
                    request.state.resource_scope = await run_in_threadpool(
                        AuthService(app.state.session_factory, settings).resource_scope,
                        actor,
                        path,
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
