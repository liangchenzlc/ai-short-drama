"""Opt-in real Celery/RabbitMQ consumption with disposable MySQL and no provider HTTP."""

import os
import threading
from uuid import uuid4

import pytest
import test_agent_runtime as runtime_tests
from celery import current_app
from celery.contrib.testing.worker import start_worker
from celery.signals import task_postrun
from sqlalchemy import select

from short_drama.agent.publisher import AgentRabbitSender
from short_drama.agent.runtime import AgentRuntime
from short_drama.core.config import Settings
from short_drama.db.readiness import assert_agent_ready, assert_identity_ready
from short_drama.domain import AgentMessage, AgentRun, AgentTurn
from short_drama.tasks import agent_worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_AGENT_INFRA_INTEGRATION") != "1",
        reason="Set RUN_AGENT_INFRA_INTEGRATION=1 for the configured local broker",
    ),
]
runtime_setup = runtime_tests.runtime_setup


def test_real_agent_worker_consumes_and_persists_without_provider_http(runtime_setup, monkeypatch):
    factory, runtime_settings = runtime_setup
    gateway = runtime_tests.MockGateway()
    runtime = AgentRuntime(
        factory, runtime_settings, gateway, runtime_tests.prepare, runtime_tests.unused_tools
    )
    # Substitute only the model boundary and this process's runtime factory. The
    # actual registered execute_agent task, runtime and broker delivery execute.
    monkeypatch.setattr(agent_worker, "runtime", lambda: runtime)
    settings = Settings(
        auth_enabled=True,
        agent_enabled=True,
        generation_queue_namespace="agent_worker_acceptance_" + uuid4().hex,
    )
    with factory() as session:
        assert_identity_ready(session.get_bind(), settings)
        assert_agent_ready(session.get_bind(), settings)
    previous_app = current_app._get_current_object()
    sender = AgentRabbitSender(settings)
    done = threading.Event()
    states = []

    def completed(task=None, args=None, state=None, **_kwargs):
        if task is not None and task.name == "short_drama.execute_agent" and args == ["12", "1"]:
            states.append(state)
            done.set()

    task_postrun.connect(completed, weak=False)
    try:
        sender.app.loader.import_default_modules()
        assert "short_drama.execute_agent" in sender.app.tasks
        with start_worker(
            sender.app,
            pool="threads",
            concurrency=1,
            queues=[sender.queue.name],
            perform_ping_check=False,
            shutdown_timeout=15,
            loglevel="ERROR",
        ):
            sender(12, 1)
            assert done.wait(30), "The isolated Agent queue was not consumed"
            assert states == ["SUCCESS"]
            with factory() as session:
                run = session.get(AgentRun, 12)
                turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == 12))
                assert run.status == "succeeded" and run.usage["decision_calls"] == 1
                assert turn.status == "succeeded" and turn.response["applied"] is True
                assert turn.response["raw"]["codec"] == "agent.raw-response"
                assert (
                    session.scalar(
                        select(AgentMessage.content).where(AgentMessage.role == "assistant")
                    )
                    == "A safe reply."
                )
            assert gateway.posts == 1 and gateway.replays == 0
        # Worker has stopped and late acknowledgement completed before cleanup.
        with sender.app.connection_for_read() as broker:
            broker.ensure_connection(max_retries=0)
            channel = broker.channel()
            try:
                assert (
                    channel.queue_declare(queue=sender.queue.name, passive=True).message_count == 0
                )
            finally:
                channel.close()
    finally:
        task_postrun.disconnect(completed)
        try:
            namespace = settings.generation_queue_namespace
            assert namespace.startswith("agent_worker_acceptance_")
            with sender.app.connection_for_write() as broker:
                broker.ensure_connection(max_retries=0)
                resources = [("queue", suffix) for suffix in (".tasks.agent", ".tasks.agent.dead")]
                resources += [("exchange", suffix) for suffix in (".tasks", ".dead")]
                for kind, suffix in resources:
                    channel = broker.channel()
                    try:
                        if kind == "queue":
                            channel.queue_delete(queue=namespace + suffix)
                        else:
                            channel.exchange_delete(exchange=namespace + suffix)
                    except Exception as error:
                        if getattr(error, "reply_code", None) != 404:
                            raise
                    finally:
                        try:
                            channel.close()
                        except Exception:
                            pass
        finally:
            sender.app.close()
            previous_app.set_current()
            previous_app.set_default()
