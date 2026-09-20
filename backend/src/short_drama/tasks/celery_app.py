"""Celery transport configuration; MySQL owns results and retry scheduling."""

import ssl

from celery import Celery
from kombu import Exchange, Queue

from short_drama.core.config import Settings


def topology(settings):
    namespace = settings.generation_queue_namespace
    prefix = "" if namespace == "short_drama" else f"{namespace}."
    exchange = Exchange(f"{namespace}.tasks", type="direct", durable=True)
    dead_exchange = Exchange(f"{namespace}.dead", type="direct", durable=True)
    queues = {
        kind: Queue(
            f"{prefix}tasks.ai.{kind}",
            exchange=exchange,
            routing_key=kind,
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": dead_exchange.name,
                "x-dead-letter-routing-key": kind,
            },
        )
        for kind in ("text", "image", "video")
    }
    return exchange, dead_exchange, queues


def make_celery(settings=None):
    settings = settings or Settings()
    exchange, _dead_exchange, queues = topology(settings)
    app = Celery(
        "short_drama",
        broker=settings.rabbitmq_url.get_secret_value(),
        include=["short_drama.tasks.worker"],
    )
    app.conf.update(
        task_queues=tuple(queues.values()),
        task_create_missing_queues=False,
        task_default_queue=queues["text"].name,
        task_default_exchange=exchange.name,
        task_default_exchange_type="direct",
        task_default_routing_key="text",
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_acks_on_failure_or_timeout=False,
        task_publish_retry=False,
        worker_prefetch_multiplier=1,
        worker_pool="threads",
        worker_enable_remote_control=False,
        worker_send_task_events=False,
        broker_connection_timeout=5,
        broker_heartbeat=30,
        broker_transport_options={"confirm_publish": True},
        broker_use_ssl={"cert_reqs": ssl.CERT_REQUIRED} if settings.rabbitmq_tls else False,
        enable_utc=True,
        timezone="UTC",
    )
    return app


app = make_celery()
