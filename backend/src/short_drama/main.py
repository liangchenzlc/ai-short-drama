from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from short_drama.api.mcp.auth import McpBearerAuth, authenticate
from short_drama.api.mcp.server import create_mcp_app
from short_drama.api.v1.router import router
from short_drama.core.branding import get_brand
from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError
from short_drama.core.logging import configure_logging
from short_drama.db.session import build_engine, session_factory
from short_drama.storage.minio import MinioStorage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        engine = build_engine(settings)
        from short_drama.db.readiness import (
            assert_agent_ready,
            assert_identity_ready,
            assert_mcp_ready,
            inspect_agent_schema,
        )

        try:
            assert_identity_ready(engine, settings)
            assert_mcp_ready(engine)
            assert_agent_ready(engine, settings)
            app.state.agent_schema_ready = False
            if settings.auth_enabled:
                with engine.connect() as connection:
                    app.state.agent_schema_ready = (
                        inspect_agent_schema(connection)["status"] == "ready"
                    )
            app.state.session_factory = session_factory(engine)
            app.state.settings = settings
            storage = MinioStorage(settings)
            app.state.storage = storage
            try:
                async with mcp_http.lifespan(mcp_http):
                    yield
            finally:
                storage.close()
        finally:
            engine.dispose()

    app = FastAPI(title=get_brand().titles.app, version="0.1.0", lifespan=lifespan)

    app.state.settings = settings
    mcp_http = create_mcp_app(app.state)
    app.mount("/mcp", mcp_http)
    app.add_middleware(McpBearerAuth, authenticate=authenticate)
    from short_drama.api.identity_middleware import install_identity

    install_identity(app, settings)

    @app.exception_handler(BusinessError)
    async def business_error(_request: Request, exc: BusinessError):
        error = {"code": exc.code, "message": exc.message}
        details = getattr(exc, "details", {})
        safe = {}
        if isinstance(details, dict):
            version = details.get("current_version")
            if isinstance(version, (str, int)) and str(version).isdigit():
                safe["current_version"] = str(version)
            count = details.get("reference_count")
            if type(count) is int and count >= 0:
                safe["reference_count"] = count
            refs = details.get("references")
            if isinstance(refs, list):
                safe["references"] = [
                    {k: str(r[k]) for k in ("type", "id", "name") if k in r}
                    if isinstance(r, dict)
                    else str(r)
                    for r in refs[:100]
                    if isinstance(r, dict) or (type(r) in (str, int) and str(r).isdigit())
                ]
        if safe:
            error["details"] = safe
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        # FastAPI's default detail includes raw input, which may contain API keys.
        fields = [
            {"field": ".".join(str(part) for part in error["loc"][1:]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "fields": fields,
                }
            },
        )

    app.include_router(router)
    return app


app = create_app()
