"""Opt-in local infrastructure only: no external model or production task is executed."""

import os
from uuid import uuid4

import pytest

import tests.support.assistant_workspace as conversation_tests
from short_drama.agent.coordinator.publisher import AgentRabbitSender
from short_drama.core.config import Settings
from short_drama.tasks.celery_app import agent_queue

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_AGENT_INFRA_INTEGRATION") != "1",
        reason="Set RUN_AGENT_INFRA_INTEGRATION=1 for the configured local broker/MinIO",
    ),
]
workspace = conversation_tests.workspace


def test_agent_publication_is_confirmed_and_routes_only_safe_identifiers():
    namespace = "agent_acceptance_" + uuid4().hex
    settings = Settings(generation_queue_namespace=namespace)
    sender = AgentRabbitSender(settings)
    try:
        sender(111, 2)
        with sender.app.connection_for_read() as broker:
            broker.ensure_connection(max_retries=0)
            channel = broker.channel()
            message = agent_queue(settings)(channel).get(no_ack=False)
            assert message is not None
            assert message.headers["task"] == "short_drama.execute_agent"
            assert message.payload[0] == ["111", "2"]
            assert message.payload[1] == {}
            assert message.delivery_info["routing_key"] == "agent"
            message.ack()
    finally:
        with sender.app.connection_for_write() as broker:
            broker.ensure_connection(max_retries=0)
            resources = [("queue", suffix) for suffix in (".tasks.agent", ".tasks.agent.dead")]
            resources += [("exchange", suffix) for suffix in (".tasks", ".dead")]
            for kind, suffix in resources:
                name = namespace + suffix
                channel = broker.channel()
                try:
                    if kind == "queue":
                        channel.queue_delete(queue=name)
                    else:
                        channel.exchange_delete(exchange=name)
                except Exception as error:
                    if getattr(error, "reply_code", None) != 404:
                        raise
                finally:
                    try:
                        channel.close()
                    except Exception:
                        pass
