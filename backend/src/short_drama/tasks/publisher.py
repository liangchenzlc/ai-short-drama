"""Publish one current action with confirms and mandatory return handling."""

from uuid import uuid4

from kombu import Producer, Queue

from short_drama.dao.task_runtime_dao import TaskRuntimeDAO
from short_drama.tasks.celery_app import make_celery, topology


class RabbitSender:
    def __init__(self, settings):
        self.app = make_celery(settings)
        self.exchange, self.dead_exchange, self.queues = topology(settings)

    def __call__(self, task_id, version, kind):
        returned = []
        with self.app.connection_for_write() as connection:
            connection.ensure_connection(max_retries=0)
            channel = connection.channel()
            Queue(
                f"{self.queues[kind].name}.dead",
                exchange=self.dead_exchange,
                routing_key=kind,
                durable=True,
            )(channel).declare()
            producer = Producer(channel, on_return=lambda *args: returned.append(True))
            message = self.app.amqp.create_task_message(
                uuid4().hex,
                "short_drama.execute_generation",
                args=[str(task_id), str(version)],
                kwargs={},
            )
            self.app.amqp.send_task_message(
                producer,
                "short_drama.execute_generation",
                message,
                exchange=self.exchange.name,
                routing_key=kind,
                queue=self.queues[kind],
                mandatory=True,
                retry=False,
                delivery_mode=2,
                confirm_timeout=5,
                timeout=5,
            )
            if returned:
                raise RuntimeError("Unroutable generation action")


class Publisher:
    def __init__(self, factory, settings, send=None):
        self.store = TaskRuntimeDAO(factory, settings)
        self.send = send or RabbitSender(settings)

    def tick(self):
        publication = self.store.claim_publish()
        if publication is None:
            return False
        try:
            self.send(publication["task_id"], publication["version"], publication["kind"])
        except Exception:
            self.store.publish_result(**publication, success=False)
        else:
            self.store.publish_result(**publication, success=True)
        return True
