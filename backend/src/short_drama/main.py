from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from short_drama.api.v1.router import router
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
        app.state.session_factory = session_factory(engine)
        app.state.settings = settings
        try:
            storage = MinioStorage(settings)
            app.state.storage = storage
            try:
                yield
            finally:
                storage.close()
        finally:
            engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    @app.exception_handler(BusinessError)
    async def business_error(_request: Request, exc: BusinessError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
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
