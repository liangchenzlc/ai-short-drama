"""Opt-in real broker delivery and physical storage cleanup in disposable namespaces."""

import io
import json
import os
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from celery import current_app
from celery.contrib.testing.worker import start_worker
from celery.signals import task_postrun
from PIL import Image
from sqlalchemy import select

import tests.integration.standard.assistant.test_assistant_memory as memory_tests
from short_drama.agent.memory.runtime import AgentMemoryPublisher, AgentMemoryRuntime
from short_drama.agent.coordinator.publisher import AgentRabbitSender
from short_drama.agent.session.purge import purge_one
from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound
from short_drama.domain import MediaFile
from short_drama.domain.agent_context import AgentAttachment
from short_drama.domain.agent_memory import AgentMemoryItem, AgentMemoryJob
from short_drama.service.agent_attachment_service import AgentAttachmentService
from short_drama.service.assistant_task_service import AssistantTaskService
from short_drama.service.storage_service import StorageService
from short_drama.storage.minio import MinioStorage
from short_drama.tasks import agent_worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_AGENT_INFRA_INTEGRATION") != "1",
        reason="Set RUN_AGENT_INFRA_INTEGRATION=1 for local RabbitMQ and MinIO",
    ),
]
memory_workspace = memory_tests.memory_workspace


def _remove_broker_namespace(sender, settings):
    namespace = settings.generation_queue_namespace
    assert namespace.startswith("assistant_memory_acceptance_")
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


def test_real_memory_worker_consumes_a_published_job_and_deduplicates_redelivery(
    memory_workspace, monkeypatch
):
    factory, _projects, _conversations = memory_workspace
    accepted = memory_tests.seeded_run(memory_workspace, count=2)
    identifier = memory_tests.maintenance_job(memory_workspace, accepted)

    class Gateway:
        calls = 0

        async def run_segment(self, _snapshot, _credential, *, on_request, on_response, **_kwargs):
            self.calls += 1
            await on_request({"fixture": "memory-infrastructure-request"})
            await on_response({"fixture": "memory-infrastructure-receipt"})
            return SimpleNamespace(
                output=json.dumps(
                    {
                        "memories": [
                            {
                                "kind": "preference",
                                "memory_key": "dialogue",
                                "title": "对白偏好",
                                "content": "对白要短",
                                "source_message_ids": [str(accepted.message.id)],
                            }
                        ]
                    }
                ),
                usage={"output_tokens": 16},
            )

    gateway = Gateway()
    runtime = AgentMemoryRuntime(factory, memory_tests.runtime_settings(), gateway)
    monkeypatch.setattr(agent_worker, "memory_runtime", lambda: runtime)
    settings = Settings(
        auth_enabled=True,
        agent_enabled=True,
        generation_queue_namespace="assistant_memory_acceptance_" + uuid4().hex,
    )
    previous_app = current_app._get_current_object()
    sender = AgentRabbitSender(settings, task_name="short_drama.maintain_agent_memory")
    publisher = AgentMemoryPublisher(factory, settings, send=sender)
    done, states = threading.Event(), []

    def completed(task=None, args=None, state=None, **_kwargs):
        if (
            task is not None
            and task.name == "short_drama.maintain_agent_memory"
            and args == [str(identifier), "1"]
        ):
            states.append(state)
            done.set()

    task_postrun.connect(completed, weak=False)
    try:
        sender.app.loader.import_default_modules()
        with start_worker(
            sender.app,
            pool="threads",
            concurrency=1,
            queues=[sender.queue.name],
            perform_ping_check=False,
            shutdown_timeout=15,
            loglevel="ERROR",
        ):
            assert publisher.tick()
            assert done.wait(30), "The isolated memory queue was not consumed"
            assert states == ["SUCCESS"] and not publisher.tick()
            with factory() as session:
                job = session.get(AgentMemoryJob, identifier)
                assert job.status == "succeeded" and job.provider_state == "received"
                assert job.attempts == 1 and job.raw_result["raw"]["fixture"]
                item = session.scalar(select(AgentMemoryItem))
                assert item.content == "对白要短" and item.content_version == 1
            done.clear()
            sender(identifier, 1)
            assert done.wait(30), "The duplicate memory delivery was not consumed"
            assert states == ["SUCCESS", "SUCCESS"] and gateway.calls == 1
        with sender.app.connection_for_read() as broker:
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
            _remove_broker_namespace(sender, settings)
        finally:
            sender.app.close()
            previous_app.set_current()
            previous_app.set_default()


def test_real_minio_conversation_purge_physically_removes_only_unreferenced_uploads(
    memory_workspace,
):
    factory, _projects, conversations = memory_workspace
    settings = Settings(auth_enabled=True, agent_enabled=True)
    prefix = "assistant-memory-purge-integration/" + uuid4().hex + "/"

    class Storage(MinioStorage):
        def __init__(self):
            super().__init__(settings)
            self.written = {}

        def stat(self, bucket, key):
            return super().stat(bucket, key if key.startswith(prefix) else prefix + key)

        def put(self, bucket, key, *args):
            key = prefix + key
            self.written[bucket, key] = None
            result = super().put(bucket, key, *args)
            self.written[bucket, key] = result.version_id
            return result

    storage = Storage()
    managed = StorageService(storage, settings)
    image = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(image, "PNG")
    data = image.getvalue()
    try:
        with factory() as session:
            session.info["actor"] = memory_tests.actor()
            library = AgentAttachmentService(session, settings, storage)
            orphan = library.upload(conversations[0], io.BytesIO(data), "orphan.png", "orphan")
            shared = library.upload(conversations[0], io.BytesIO(data), "shared.png", "shared")
            library.reference(
                conversations[1], {"source_type": "media", "source_id": shared.media_id}, "keep"
            )
            tasks = AssistantTaskService(session, settings)
            current = tasks.get_conversation(conversations[0])
            tasks.delete_global_conversation(
                conversations[0], {"row_version": current.row_version}, "purge"
            )
        with factory() as session:
            orphan_location = session.get(MediaFile, orphan.media_id).storage_locator
            shared_location = session.get(MediaFile, shared.media_id).storage_locator
        assert managed.stat(orphan_location).size == len(data)
        assert purge_one(factory, managed) and not purge_one(factory, managed)
        with pytest.raises(NotFound):
            managed.stat(orphan_location)
        with managed.open(shared_location) as response:
            assert response.read() == data
        with factory() as session:
            assert session.get(MediaFile, orphan.media_id) is None
            assert session.get(MediaFile, shared.media_id) is not None
            assert len(session.scalars(select(AgentAttachment)).all()) == 1
            job = session.scalar(select(AgentMemoryJob).where(AgentMemoryJob.kind == "purge"))
            assert job.status == "succeeded"
        orphan_key = orphan_location.split("/", 3)[-1]
        assert orphan_key.startswith(prefix)
        assert not list(
            storage.client.list_objects(
                settings.minio_image_bucket, prefix=orphan_key, include_version=True
            )
        ), "The purged upload still has a physical object version"
    finally:
        try:
            for (bucket, key), version in storage.written.items():
                assert key.startswith(prefix)
                storage.remove(bucket, key, version_id=version)
        finally:
            storage.close()
