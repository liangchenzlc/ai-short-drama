"""Opt-in local infrastructure only: no external model or production task is executed."""

import os
from uuid import uuid4

import httpx
import pytest
import test_agent_conversations as conversation_tests
from sqlalchemy import select
from test_agent_conversations import actor
from test_agent_native_tasks import Provider, admit, drain, prepared

from short_drama.agent.native_tasks import collect_native_results
from short_drama.agent.publisher import AgentRabbitSender
from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound
from short_drama.domain import AgentArtifact, MediaFile
from short_drama.service.agent_artifact_service import AgentArtifactService
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.storage.minio import MinioStorage
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


class TrackedStorage(MinioStorage):
    def __init__(self, settings):
        super().__init__(settings)
        self.written = {}

    def put(self, bucket, key, *args):
        self.written[bucket, key] = None
        stored = super().put(bucket, key, *args)
        self.written[bucket, key] = stored.version_id
        return stored


def test_native_worker_archives_in_real_minio_then_shares_and_adopts(workspace):
    flow = prepared(workspace, "image")
    # The provider remains an in-memory deterministic PNG; storage and MySQL are real.
    flow.settings = Settings(auth_enabled=True, agent_enabled=True)
    storage = TrackedStorage(flow.settings)
    task_id = None
    try:
        task_id = admit(flow)
        worker = GenerationExecutionService(flow.factory, flow.settings, Provider("image"), storage)
        assert drain(flow, task_id, worker) == "succeeded"
        assert collect_native_results(flow.factory, flow.settings) == 1
        with flow.factory() as session:
            artifact = session.scalar(select(AgentArtifact))
            media = session.get(MediaFile, artifact.media_id)
            identifier, locator, byte_size = artifact.id, media.storage_locator, media.byte_size
            session.rollback()
            session.info["actor"] = actor(1)
            svc = AgentArtifactService(session, flow.settings, storage)
            detail = svc.get(flow.project_id, flow.episode_id, identifier)
            source = detail["source_snapshot"]
            applied = svc.adopt(
                flow.project_id,
                flow.episode_id,
                identifier,
                {
                    "row_version": 1,
                    "content_version": source["content_version"],
                    "target_row_version": source["target_row_version"],
                    "confirm_shared": True,
                },
            )
            assert applied["status"] == "applied"
            from short_drama.service.storage_service import StorageService

            service = StorageService(storage, flow.settings)
            assert service.stat(locator).size == byte_size
            # Do not print this temporary access URL.
            response = httpx.get(service.download_url(locator), timeout=10, trust_env=False)
            assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
    finally:
        try:
            for (bucket, key), version in storage.written.items():
                assert task_id is not None and key.startswith(f"generations/{task_id}/")
                if version is None:
                    try:
                        version = storage.stat(bucket, key).version_id
                    except NotFound:
                        continue
                storage.remove(bucket, key, version)
        finally:
            storage.close()
