from types import SimpleNamespace

import pytest

from short_drama.agent import publisher
from short_drama.agent.recovery import recover
from short_drama.agent.runtime import AgentRuntimeStore, _DeltaBuffer
from short_drama.agent.state import budget_limits
from short_drama.tasks.celery_app import agent_queue, make_celery, topology


def test_agent_disabled_never_reads_new_tables():
    settings = SimpleNamespace(agent_enabled=False)
    assert recover(None, settings) == 0
    store = AgentRuntimeStore(None, settings)
    assert store.claim_publish() is None
    assert store.claim_execution(1, 1, None) is None


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
    assert "short_drama.tasks.agent_worker" in app.conf.include


def test_reviewed_workflow_has_only_the_documented_higher_limits():
    assert budget_limits()["decision_calls"] == 8
    assert budget_limits()["output_tokens"] == 32768
    assert budget_limits("workflow")["decision_calls"] == 16
    assert budget_limits("workflow")["tool_calls"] == 32
    assert budget_limits("workflow")["output_tokens"] == 65536
    assert budget_limits("workflow")["active_seconds"] == 600


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


def test_unroutable_agent_publication_is_reported_and_contains_only_stable_ids(monkeypatch):
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
        return {"args": args}

    def send_message(producer, name, message, **kwargs):
        sent.append(kwargs)
        callbacks["on_return"]()

    monkeypatch.setattr(publisher, "Queue", DeclaredQueue)
    monkeypatch.setattr(publisher, "Producer", producer)
    sender = object.__new__(publisher.AgentRabbitSender)
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
