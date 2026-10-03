"""Dedicated Agent worker; broker retries cannot resubmit a model request."""

from functools import lru_cache

from celery.exceptions import Reject

from short_drama.agent.model_gateway import AgentModelGateway
from short_drama.agent.runtime import AgentRuntime
from short_drama.core.config import Settings
from short_drama.db.readiness import assert_agent_ready, assert_identity_ready
from short_drama.db.session import build_engine, session_factory
from short_drama.tasks.celery_app import app


@lru_cache(maxsize=1)
def runtime():
    settings = Settings()
    engine = build_engine(settings)
    assert_identity_ready(engine, settings)
    assert_agent_ready(engine, settings)
    return AgentRuntime(session_factory(engine), settings, AgentModelGateway(settings))


@app.task(
    name="short_drama.execute_agent", acks_late=True, reject_on_worker_lost=True, ignore_result=True
)
def execute_agent(run_id, message_version):
    try:
        runtime().execute_one(run_id, message_version)
    except Exception:
        raise Reject(
            "Agent action interrupted; database recovery will reconcile", requeue=False
        ) from None
