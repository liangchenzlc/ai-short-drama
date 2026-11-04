import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from short_drama.agent.coordinator import publisher
from short_drama.agent.coordinator.recovery import recover
from short_drama.agent.runtime.delta_buffer import _DeltaBuffer
from short_drama.tasks.celery_app import agent_queue, make_celery, topology


def test_lease_renewal_jitter_does_not_cancel_valid_execution(caplog):
    from short_drama.agent.runtime.lease import run_with_lease
    from short_drama.core.time import utcnow

    attempts = []

    async def renew():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("controlled renewal outage")
        return utcnow() + timedelta(seconds=1)

    async def exercise():
        async def graph():
            await asyncio.sleep(0.06)
            return "completed"

        return await run_with_lease(
            graph(), renew=renew, interval=0.01, lease_until=utcnow() + timedelta(seconds=1)
        )

    assert asyncio.run(exercise()) == "completed"
    assert len(attempts) >= 2
    assert any(
        "duration_ms=" in record.getMessage() and "remaining_ms=" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize("failure", ["outage", "blocked"])
def test_lease_renewal_deadline_cancels_graph_and_is_recoverable(failure):
    from short_drama.agent.runtime.executor import recoverable_persistence_error
    from short_drama.agent.runtime.lease import LeaseRenewalUnavailable, run_with_lease
    from short_drama.core.time import utcnow

    cancelled = []

    async def graph():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    async def renew():
        if failure == "blocked":
            await asyncio.sleep(10)
        raise OSError("controlled renewal failure")

    async def exercise():
        await run_with_lease(
            graph(), renew=renew, interval=0.01, lease_until=utcnow() + timedelta(seconds=0.08)
        )

    with pytest.raises(LeaseRenewalUnavailable) as caught:
        asyncio.run(exercise())
    assert cancelled == [True]
    assert recoverable_persistence_error(caught.value)


@pytest.mark.parametrize("boundary", ["lease", "stop"])
def test_confirmed_lease_loss_and_stop_preserve_cancellation_over_cleanup_errors(boundary):
    from short_drama.agent.core.access import LeaseLost
    from short_drama.agent.runtime.lease import run_with_lease
    from short_drama.core.time import utcnow

    async def graph():
        try:
            await asyncio.sleep(10)
        finally:
            raise RuntimeError("controlled graph cancellation cleanup failure")

    async def renew():
        return False if boundary == "lease" else utcnow() + timedelta(seconds=1)

    class Sandbox:
        async def cancel(self):
            raise OSError("controlled sandbox cancellation failure")

    checks = []

    async def control():
        checks.append(True)
        if len(checks) == 1:
            raise OSError("controlled read jitter")
        return False

    async def exercise():
        await run_with_lease(
            graph(),
            renew=renew,
            interval=0.01,
            lease_until=utcnow() + timedelta(seconds=1),
            sandbox=Sandbox(),
            control=control if boundary == "stop" else None,
            control_interval=0.01,
        )

    with pytest.raises(LeaseLost):
        asyncio.run(exercise())


def test_external_cancellation_is_not_reported_as_lease_loss():
    from short_drama.agent.runtime.lease import run_with_lease
    from short_drama.core.time import utcnow

    async def exercise():
        async def renew():
            return utcnow() + timedelta(seconds=1)

        task = asyncio.create_task(
            run_with_lease(
                asyncio.sleep(10),
                renew=renew,
                interval=0.5,
                lease_until=utcnow() + timedelta(seconds=1),
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_already_expired_claim_never_starts_local_execution():
    from short_drama.agent.runtime.lease import LeaseRenewalUnavailable, run_with_lease
    from short_drama.core.time import utcnow

    started = []

    async def graph():
        started.append(True)
        return "must not execute"

    async def renew():
        raise AssertionError("expired claim cannot renew")

    async def exercise():
        await run_with_lease(
            graph(), renew=renew, interval=1, lease_until=utcnow() - timedelta(seconds=1)
        )

    with pytest.raises(LeaseRenewalUnavailable):
        asyncio.run(exercise())
    assert started == []


@pytest.mark.parametrize("task_status", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("tool_status", ["waiting_generation", "cancelled"])
def test_native_collector_selects_finished_tasks_past_active_prefix(
    monkeypatch, task_status, tool_status
):
    from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine
    from sqlalchemy.orm import sessionmaker

    from short_drama.agent.native import tasks as native_tasks

    engine = create_engine("sqlite://")
    metadata = MetaData()
    tasks = Table(
        "async_tasks", metadata, Column("id", Integer, primary_key=True), Column("status", String)
    )
    tools = Table(
        "agent_tool_calls",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("run_id", Integer),
        Column("generation_task_id", Integer),
        Column("status", String),
        Column("result", JSON),
    )
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    selected_runs = []

    def skip_locked_run(_session, run_id, *, skip_locked):
        assert skip_locked
        selected_runs.append(run_id)
        return None

    monkeypatch.setattr(native_tasks, "lock_run", skip_locked_run)
    try:
        with engine.begin() as connection:
            connection.execute(
                tasks.insert(),
                [
                    {"id": identifier, "status": "running" if identifier <= 100 else task_status}
                    for identifier in range(1, 102)
                ],
            )
            connection.execute(
                tools.insert(),
                [
                    {
                        "id": identifier,
                        "run_id": identifier,
                        "generation_task_id": identifier,
                        "status": tool_status,
                        "result": {"settled": False},
                    }
                    for identifier in range(1, 102)
                ],
            )
        assert native_tasks.collect_native_results(factory, SimpleNamespace(), limit=100) == 0
        assert selected_runs == [101]
    finally:
        engine.dispose()


def test_agent_disabled_keeps_workspace_maintenance_and_skips_new_decisions(monkeypatch):
    from short_drama.agent.sandbox import workspace

    cleaned = []
    monkeypatch.setattr(workspace, "cleanup_terminal_runs", lambda *args: cleaned.append(args))
    settings = SimpleNamespace(agent_enabled=False)
    assert recover(None, settings) == 0
    assert cleaned == [(None, settings, 100)]
    from short_drama.service.assistant.execution.run import AgentRunService

    assert AgentRunService(None, settings).claim(1, 1) is None


def test_worker_dispatches_agent_delivery_to_framework_runtime(monkeypatch):
    from short_drama.tasks.assistant import agent_worker

    factory, settings = object(), object()
    deliveries = []

    class Runtime:
        def __init__(self, received_factory, received_settings):
            assert received_factory is factory and received_settings is settings

        def execute(self, run_id, message_version):
            deliveries.append((run_id, message_version))

    monkeypatch.setattr(agent_worker, "runtime", lambda: (factory, settings))
    monkeypatch.setattr(agent_worker, "AgentRuntime", Runtime)
    agent_worker.execute_agent.run("12", "3")
    assert deliveries == [("12", "3")]


def test_agent_queue_is_distinct_and_uses_confirmed_no_retry_transport():
    from short_drama.core.config import Settings

    settings = Settings(
        auth_enabled=False, agent_enabled=False, generation_queue_namespace="test_agent"
    )
    _exchange, _dead, native = topology(settings)
    queue = agent_queue(settings)
    assert queue.name not in {item.name for item in native.values()}
    assert queue.routing_key == "agent"
    assert queue.queue_arguments["x-dead-letter-routing-key"] == "agent"
    app = make_celery(settings)
    assert app.conf.task_publish_retry is False
    assert app.conf.broker_transport_options["confirm_publish"] is True
    assert "short_drama.tasks.assistant.agent_worker" in app.conf.include


def test_stream_display_preserves_gateway_redaction_without_a_second_replace():
    class Store:
        def __init__(self):
            self.parts = []

        def save_delta(self, claim, delta, offset):
            self.parts.append((delta, offset))
            return True

    store = Store()
    buffer = _DeltaBuffer(store, None)
    for chunk in ("A ", "[redacted]", " is safe", "."):
        buffer.add(chunk)
    buffer.add("", final=True)
    assert "".join(item[0] for item in store.parts) == "A [redacted] is safe."


@pytest.mark.parametrize(
    "task_name", ["short_drama.execute_agent", "short_drama.maintain_agent_memory"]
)
def test_unroutable_agent_publication_is_reported_and_contains_only_stable_ids(
    monkeypatch, task_name
):
    callbacks, sent = {}, []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ensure_connection(self, max_retries):
            assert max_retries == 0

        def channel(self):
            return "channel"

    class DeclaredQueue:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, channel):
            return self

        def declare(self):
            pass

    def producer(channel, on_return):
        callbacks["on_return"] = on_return
        return "producer"

    def create_message(task_id, name, args, kwargs):
        assert args == ["12", "4"] and kwargs == {}
        assert name == task_name
        return {"args": args}

    def send_message(producer, name, message, **kwargs):
        sent.append(kwargs)
        callbacks["on_return"]()

    monkeypatch.setattr(publisher, "Queue", DeclaredQueue)
    monkeypatch.setattr(publisher, "Producer", producer)
    sender = object.__new__(publisher.AgentRabbitSender)
    sender.task_name = task_name
    sender.app = SimpleNamespace(
        connection_for_write=Connection,
        amqp=SimpleNamespace(create_task_message=create_message, send_task_message=send_message),
    )
    sender.exchange, sender.dead_exchange = (
        SimpleNamespace(name="tasks"),
        SimpleNamespace(name="dead"),
    )
    sender.queue = SimpleNamespace(name="agent")
    with pytest.raises(RuntimeError, match="Unroutable Agent"):
        sender(12, 4)
    assert sent[0]["mandatory"] and sent[0]["retry"] is False
    assert sent[0]["routing_key"] == "agent"
