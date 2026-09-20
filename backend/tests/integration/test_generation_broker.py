"""Opt-in real RabbitMQ/Celery/MySQL/MinIO pipeline, with a local model fixture.

Only queues with this test's UUID namespace and objects it created are removed.
No paid provider requests and no business-schema writes.
"""

import base64
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from uuid import uuid4

import pytest
from celery.contrib.testing.worker import start_worker
from kombu import Queue
from PIL import Image
from sqlalchemy import select

from short_drama.ai import GenerationGateway
from short_drama.core.config import Settings
from short_drama.db.session import session_factory
from short_drama.domain import AIGenerationRecord, AIModelConfig, AsyncTask, MediaAsset, MediaFile
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.storage.minio import MinioStorage
from short_drama.storage.models import ObjectLocation
from short_drama.tasks.celery_app import make_celery, topology
from short_drama.tasks.publisher import Publisher
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.getenv("RUN_GENERATION_BROKER_TESTS") != "1", reason="Broker test opt-in")
def test_three_generation_actions_cross_real_broker_and_archive(
    mysql_engine, db_session, monkeypatch
):
    from short_drama.tasks import worker

    image_bytes = BytesIO()
    Image.new("RGB", (8, 6), "orange").save(image_bytes, "PNG")
    # Container header is a protocol fixture, not a claim of playable vendor video.
    video_bytes = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2"
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            posts.append((self.path, body))
            if self.path.endswith("/chat/completions"):
                result = {
                    "choices": [
                        {"message": {"content": "消息队列联调文本"}, "finish_reason": "stop"}
                    ]
                }
            elif self.path.endswith("/images/generations"):
                result = {"data": [{"b64_json": base64.b64encode(image_bytes.getvalue()).decode()}]}
            else:
                result = {"id": "fixture-video"}
            self.respond(json.dumps(result).encode(), "application/json")

        def do_GET(self):
            if self.path.endswith("/fixture.mp4"):
                self.respond(video_bytes, "video/mp4")
            else:
                result = {
                    "id": "fixture-video",
                    "status": "succeeded",
                    "content": {
                        "video_url": f"http://127.0.0.1:{self.server.server_port}/fixture.mp4",
                    },
                }
                self.respond(json.dumps(result).encode(), "application/json")

        def respond(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    namespace = "generation_test_" + uuid4().hex
    settings = Settings().model_copy(
        update={
            "generation_queue_namespace": namespace,
            "model_discovery_allowed_hosts": ["127.0.0.1"],
            "generation_poll_seconds": 3,
        }
    )
    factory = session_factory(mysql_engine)
    storage = MinioStorage(settings)
    executor = GenerationExecutionService(factory, settings, GenerationGateway(settings), storage)
    monkeypatch.setattr(worker, "execution_service", lambda: executor)
    app = make_celery(settings)
    exchange, dead_exchange, queues = topology(settings)
    publisher = Publisher(factory, settings)
    ids = []
    try:
        storage.check_buckets()
        with factory.begin() as session:
            for kind in ("text", "image", "video"):
                path = "/api/v3/contents/generations/tasks" if kind == "video" else "/v1"
                config = AIModelConfig(
                    id=next_id(),
                    service_type=kind,
                    name="pipeline fixture",
                    model_key="fixture",
                    provider="fixture",
                    base_url=f"http://127.0.0.1:{server.server_port}{path}",
                )
                session.add(config)
                ids.append((kind, config.id))
        tasks = []
        for kind, config_id in ids:
            payload = {
                "config_id": str(config_id),
                "input": {
                    "messages": [{"role": "user", "content": "test"}],
                }
                if kind == "text"
                else {"prompt": "fixture media"},
                "parameters": {},
            }
            with factory() as session:
                created, _ = AIGenerationService(session, settings).create(
                    kind, payload, str(uuid4())
                )
                tasks.append(int(created["generation_id"]))
        with start_worker(
            app,
            perform_ping_check=False,
            pool="threads",
            concurrency=2,
            queues=[q.name for q in queues.values()],
            loglevel="CRITICAL",
            shutdown_timeout=20,
        ):
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                publisher.tick()
                with factory() as session:
                    states = list(
                        session.scalars(select(AsyncTask.status).where(AsyncTask.id.in_(tasks)))
                    )
                if len(states) == 3 and all(status == "succeeded" for status in states):
                    break
                time.sleep(0.15)
            assert states == ["succeeded"] * 3, states
            with factory() as session:
                records = list(
                    session.scalars(
                        select(AIGenerationRecord).where(AIGenerationRecord.task_id.in_(tasks))
                    )
                )
                assert len(records) == 3
                assert any(record.text_content == "消息队列联调文本" for record in records)
                assets = list(session.scalars(select(MediaAsset)))
                assert sorted(asset.media_type for asset in assets) == ["image", "video"]
                image_media = session.get(
                    MediaFile, next(a.media_id for a in assets if a.media_type == "image")
                )
                assert (image_media.width, image_media.height) == (8, 6)
            assert len(posts) == 3, "A completed action was submitted more than once"
    finally:
        # Prefix comes solely from a generated namespace; never touch application queues.
        try:
            with app.connection_for_write() as connection:
                channel = connection.channel()
                for queue in queues.values():
                    queue(channel).delete(if_unused=False, if_empty=False)
                    Queue(queue.name + ".dead", channel=channel).delete(
                        if_unused=False, if_empty=False
                    )
                exchange(channel).delete(if_unused=False)
                dead_exchange(channel).delete(if_unused=False)
        finally:
            with factory() as session:
                for media in session.scalars(select(MediaFile)):
                    location = ObjectLocation.parse(
                        media.storage_locator,
                        {settings.minio_image_bucket, settings.minio_video_bucket},
                    )
                    assert location.object_name.startswith("generations/")
                    storage.remove(location.bucket, location.object_name)
            storage.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)
