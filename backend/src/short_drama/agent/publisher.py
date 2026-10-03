"""Agent-only durable RabbitMQ publication, fenced by the Run message version."""

from uuid import uuid4

from kombu import Producer, Queue

from short_drama.agent.runtime import AgentRuntimeStore
from short_drama.tasks.celery_app import agent_queue, make_celery, topology


class AgentRabbitSender:
    def __init__(self, settings):
        self.app = make_celery(settings)
        self.exchange, self.dead_exchange, _queues = topology(settings)
        self.queue = agent_queue(settings)

    def __call__(self, run_id, version):
        returned = []
        with self.app.connection_for_write() as connection:
            connection.ensure_connection(max_retries=0)
            channel = connection.channel()
            Queue(
                f"{self.queue.name}.dead",
                exchange=self.dead_exchange,
                routing_key="agent",
                durable=True,
            )(channel).declare()
            producer = Producer(channel, on_return=lambda *args: returned.append(True))
            message = self.app.amqp.create_task_message(
                uuid4().hex,
                "short_drama.execute_agent",
                args=[str(run_id), str(version)],
                kwargs={},
            )
            self.app.amqp.send_task_message(
                producer,
                "short_drama.execute_agent",
                message,
                exchange=self.exchange.name,
                routing_key="agent",
                queue=self.queue,
                mandatory=True,
                retry=False,
                delivery_mode=2,
                confirm_timeout=5,
                timeout=5,
            )
            if returned:
                raise RuntimeError("Unroutable Agent action")


class AgentPublisher:
    def __init__(self, factory, settings, send=None):
        self.store = AgentRuntimeStore(factory, settings)
        self.send = send or AgentRabbitSender(settings)

    def tick(self):
        publication = self.store.claim_publish()
        if publication is None:
            return False
        try:
            self.send(publication["run_id"], publication["version"])
        except Exception:
            self.store.publish_result(**publication, success=False)
        else:
            self.store.publish_result(**publication, success=True)
        return True
