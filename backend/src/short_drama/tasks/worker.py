"""Celery wrapper acknowledges only a completed durable action."""

from functools import lru_cache

from celery.exceptions import Reject

from short_drama.ai import GenerationGateway
from short_drama.core.config import Settings
from short_drama.db.session import build_engine, session_factory
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.storage.minio import MinioStorage
from short_drama.tasks.celery_app import app


@lru_cache(maxsize=1)
def execution_service():
    settings = Settings()
    engine = build_engine(settings)
    from short_drama.db.readiness import assert_identity_ready

    assert_identity_ready(engine, settings)
    return GenerationExecutionService(
        session_factory(engine),
        settings,
        GenerationGateway(settings),
        MinioStorage(settings),
    )


@app.task(
    name="short_drama.execute_generation",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def execute_generation(task_id, message_version):
    try:
        execution_service().execute(task_id, message_version)
    except Exception:
        # Broker dead letter plus DB lease recovery, never automatic model resubmission.
        raise Reject(
            "Action interrupted; database recovery will reconcile", requeue=False
        ) from None


@lru_cache(maxsize=1)
def render_service():
    from short_drama.tasks.render import RenderExecutor

    settings = Settings()
    engine = build_engine(settings)
    from short_drama.db.readiness import assert_identity_ready

    assert_identity_ready(engine, settings)
    return RenderExecutor(session_factory(engine), settings, MinioStorage(settings))


@app.task(
    name="short_drama.execute_render",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def execute_render(job_id, message_version):
    render_service().execute(job_id, message_version)
