"""Dedicated Agent worker; broker retries cannot resubmit a model request."""

from functools import lru_cache

from celery.exceptions import Reject

from short_drama.agent.gateway.langchain import LangChainGateway
from short_drama.agent.harness.runtime import HarnessRuntime
from short_drama.agent.memory.runtime import AgentMemoryRuntime
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
    return session_factory(engine), settings


@app.task(
    name="short_drama.execute_agent", acks_late=True, reject_on_worker_lost=True, ignore_result=True
)
def execute_agent(run_id, message_version):
    try:
        factory, settings = runtime()
        HarnessRuntime(factory, settings).execute_one(run_id, message_version)
    except Exception:
        raise Reject(
            "Agent action interrupted; database recovery will reconcile", requeue=False
        ) from None


@lru_cache(maxsize=1)
def memory_runtime():
    factory, settings = runtime()
    return AgentMemoryRuntime(factory, settings, LangChainGateway(settings))


@app.task(
    name="short_drama.maintain_agent_memory",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def maintain_agent_memory(job_id, message_version):
    try:
        memory_runtime().execute_one(job_id, message_version)
    except Exception:
        raise Reject(
            "Memory maintenance interrupted; database recovery will reconcile", requeue=False
        ) from None
