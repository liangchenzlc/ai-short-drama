"""模型连接测试的四类真实 broker/HTTP/媒体链；不请求付费供应商。"""

import base64
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from amqp.exceptions import NotFound as BrokerObjectMissing
from kombu import Queue
from sqlalchemy import func, select

from short_drama.ai.canvas_credentials import decode_canvas_credentials
from short_drama.core.crypto import KeyCipher
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    MediaFile,
)
from short_drama.tasks.celery_app import make_celery, topology
from short_drama.tasks.publisher import Publisher
from tests.integration.test_canvas_generation_broker import (
    eventually,
    wait_for_consumers_to_detach,
    worker_environment,
)
from tests.integration.test_canvas_generation_media_runtime import playable_fixture
from tests.integration.test_canvas_resources import canvas, png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable actual isolated model-test Celery/AMQP/HTTP/MySQL/MinIO verification",
    ),
]
TESTS = "/api/v1/canvas-runtime/model-tests"
TASKS = "/api/v1/canvas-runtime/tasks"
API_KEY = "model-broker-placeholder-not-a-real-key"
HEADER_NAME = "X-Provider-Fixture-Key"
HEADER_VALUE = "model-broker-placeholder-private-header"
TEXT = "模型测试来自实际非流式 HTTP 响应"


@pytest.fixture
def model_broker(resource_app, tmp_path):
    app, factory, current = resource_app
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    namespace = "canvas_model_broker_test_" + uuid4().hex
    assert re.fullmatch(r"canvas_model_broker_test_[a-f0-9]{32}", namespace)
    settings = current.model_copy(
        update={
            "generation_queue_namespace": namespace,
            "model_discovery_allowed_hosts": ["127.0.0.1"],
            "generation_poll_seconds": 3,
            "snowflake_worker_id": 1022,
        }
    )
    app.state.settings = settings
    media = {
        "image": png("orange"),
        "video": playable_fixture(tmp_path, "video"),
        "audio": playable_fixture(tmp_path, "audio"),
    }
    calls, supplier_errors, publisher_errors = [], [], []
    stopped = threading.Event()

    class Supplier(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                # 只保留布尔校验结果，Authorization 和自定义密钥不进入日志或证据文件。
                auth = self.headers.get("Authorization") == "Bearer " + API_KEY
                custom = self.headers.get(HEADER_NAME) == HEADER_VALUE
                calls.append({"method": "POST", "path": self.path, "body": body, "auth": auth})
                assert auth and custom, "Frozen private authentication did not reach the supplier"
                if self.path == "/v1/chat/completions":
                    assert body["stream"] is False
                    self.respond(
                        json.dumps(
                            {"choices": [{"message": {"content": TEXT}, "finish_reason": "stop"}]}
                        ).encode(),
                        "application/json",
                    )
                elif self.path == "/v1/images/generations":
                    self.respond(
                        json.dumps(
                            {"data": [{"b64_json": base64.b64encode(media["image"]).decode()}]}
                        ).encode(),
                        "application/json",
                    )
                elif self.path == "/api/v3/contents/generations/tasks":
                    self.respond(b'{"id":"model-broker-video"}', "application/json")
                elif self.path == "/v1/audio/speech":
                    self.respond(media["audio"], "audio/mpeg")
                else:
                    self.send_error(404)
            except BaseException as error:
                supplier_errors.append(type(error).__name__)
                self.close_connection = True

        def do_GET(self):
            if self.path == "/api/v3/contents/generations/tasks/model-broker-video":
                assert self.headers.get("Authorization") == "Bearer " + API_KEY
                assert self.headers.get(HEADER_NAME) == HEADER_VALUE
                calls.append({"method": "GET", "path": self.path})
                self.respond(
                    json.dumps(
                        {
                            "id": "model-broker-video",
                            "status": "succeeded",
                            "content": {
                                "video_url": f"http://127.0.0.1:{self.server.server_port}/movie.mp4"
                            },
                        }
                    ).encode(),
                    "application/json",
                )
            elif self.path == "/movie.mp4":
                # 源实现会给同 origin 下载携带冻结鉴权；跨 origin 下载另有匿名合同。
                assert self.headers.get("Authorization") == "Bearer " + API_KEY
                assert self.headers.get(HEADER_NAME) == HEADER_VALUE
                calls.append({"method": "GET", "path": self.path})
                self.respond(media["video"], "video/mp4")
            else:
                self.send_error(404)

        def respond(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    supplier = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    supplier_thread = threading.Thread(target=supplier.serve_forever, daemon=True)
    supplier_thread.start()
    celery = make_celery(settings)
    exchange, dead_exchange, queues = topology(settings)
    publisher = Publisher(factory, settings)
    worker = None
    publication_thread = None
    cleanup = {"namespace": namespace, "worker_stopped": False, "queues_absent": False}

    def publish():
        try:
            while not stopped.wait(0.02):
                publisher.tick()
        except BaseException as error:
            publisher_errors.append(type(error).__name__)

    def queue_state():
        assert worker.poll() is None, "Task-owned four-capability Celery process exited"
        with celery.connection_for_read() as connection:
            channel = connection.channel()
            return [channel.queue_declare(queue.name, passive=True) for queue in queues.values()]

    try:
        with celery.connection_for_write() as connection:
            channel = connection.channel()
            for queue in queues.values():
                queue(channel).declare()
                Queue(
                    queue.name + ".dead",
                    exchange=dead_exchange,
                    routing_key=queue.routing_key,
                    durable=True,
                )(channel).declare()
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "short_drama.tasks.celery_app:app",
                "worker",
                "--pool=threads",
                "--concurrency=2",
                "--queues=" + ",".join(queue.name for queue in queues.values()),
                "--hostname=" + namespace + "@%h",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                "--loglevel=CRITICAL",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=worker_environment(settings, factory),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        eventually(
            queue_state, lambda rows: all(row.consumer_count == 1 for row in rows), timeout=25
        )
        publication_thread = threading.Thread(target=publish, daemon=True)
        publication_thread.start()
        yield SimpleNamespace(
            app=(app, factory, settings),
            url=f"http://127.0.0.1:{supplier.server_port}",
            media=media,
            calls=calls,
        )
        assert not supplier_errors, supplier_errors
        assert not publisher_errors, publisher_errors
        eventually(
            queue_state, lambda rows: all(row.message_count == 0 for row in rows), timeout=10
        )
    finally:
        stopped.set()
        if publication_thread is not None:
            publication_thread.join(10)
            assert not publication_thread.is_alive()
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(10)
        cleanup["worker_stopped"] = worker is None or worker.poll() is not None
        try:
            assert cleanup["worker_stopped"]
            cleanup["consumer_detach_wait_ms"] = wait_for_consumers_to_detach(
                celery, queues.values()
            )
            with celery.connection_for_write() as connection:
                channel = connection.channel()
                for queue in queues.values():
                    assert queue.name.startswith(namespace + ".")
                    queue(channel).delete(if_unused=True, if_empty=False)
                    Queue(queue.name + ".dead", channel=channel).delete(
                        if_unused=True, if_empty=False
                    )
                assert exchange.name == namespace + ".tasks"
                assert dead_exchange.name == namespace + ".dead"
                exchange(channel).delete(if_unused=True)
                dead_exchange(channel).delete(if_unused=True)
                for queue in queues.values():
                    for name in (queue.name, queue.name + ".dead"):
                        check = connection.channel()
                        try:
                            with pytest.raises(BrokerObjectMissing):
                                check.queue_declare(name, passive=True)
                        finally:
                            check.close()
                cleanup["queues_absent"] = True
        finally:
            supplier.shutdown()
            supplier.server_close()
            supplier_thread.join(5)
            assert not supplier_thread.is_alive()
            cleanup["http_supplier_stopped"] = True
            evidence = Path(__file__).resolve().parents[3] / ".runtime" / "canvas-broker-runtime"
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / (namespace + ".json")).write_text(
                json.dumps(cleanup, indent=2) + "\n", encoding="utf-8"
            )


@pytest.mark.parametrize("kind", ["text", "image", "video", "audio"])
def test_model_test_four_capabilities_use_real_broker_and_author_private_download(
    model_broker, kind
):
    runtime = model_broker
    owner, user = account(runtime.app, "model_broker_author")
    member, other = account(runtime.app, "model_broker_member")
    project, path, _ = canvas(owner, "model-broker-membership")
    join(runtime.app, owner, member, project["id"], other["id"])
    before = owner.get(path + "/my-document").json()
    model, protocol = {
        "text": ("gpt-4o-mini", "chat-completion"),
        "image": ("gpt-image-1", "openai-image"),
        "video": ("doubao-seedance-1-0-pro-250528", "volcengine-ark-video"),
        "audio": ("gpt-4o-mini-tts", "openai-audio"),
    }[kind]
    body = {
        "channel": {
            "id": "model-broker-channel",
            "name": "真实测试临时渠道",
            "baseUrl": runtime.url + ("/api/v3" if kind == "video" else "/v1"),
            "apiKey": API_KEY,
            "headers": [{"name": HEADER_NAME, "value": HEADER_VALUE}],
            "apiFormat": "openai",
            "models": [model],
            "modelProfiles": [{"model": model, "capability": kind, "protocol": protocol}],
        },
        "mode": kind,
        "model": model,
        "prompt": "模型连接测试的实际生成",
        "config": {
            "text": {},
            "image": {"count": "1", "size": "1:1"},
            "video": {"size": "16:9", "videoSeconds": "5", "vquality": "720"},
            "audio": {"audioVoice": "alloy", "audioFormat": "mp3", "audioSpeed": "1"},
        }[kind],
        "textOptions": {"stream": False, "thinking": False},
        "clientOperationId": uuid4().hex,
    }
    accepted = owner.post(TESTS, json=body, headers={"Idempotency-Key": body["clientOperationId"]})
    assert accepted.status_code == 202, accepted.text
    identifier = accepted.json()["id"]
    replay = owner.post(TESTS, json=body, headers={"Idempotency-Key": body["clientOperationId"]})
    assert replay.status_code == 200 and replay.json()["id"] == identifier

    def read():
        response = owner.get(TESTS + "/" + identifier)
        assert response.status_code == 200, response.text
        return response.json()

    completed = eventually(read, lambda row: row["status"] in {"succeeded", "failed"}, timeout=50)
    assert completed["status"] == "succeeded", completed
    assert len([item for item in runtime.calls if item["method"] == "POST"]) == 1
    assert owner.get(path + "/my-document").json() == before
    projected = owner.get(TASKS + "/" + identifier)
    assert projected.status_code == 200, projected.text
    detail = projected.json()
    assert detail["resultState"] == "READY" and "projectId" not in detail
    assert json.loads(detail["resultJson"]) == completed["result"]
    assert detail["clientContext"] == {"source": "model-connection-test"}
    assert owner.get(TASKS + "/" + identifier + "/logs").json()
    assert [item["id"] for item in owner.get(TASKS).json()] == [identifier]
    assert member.get(TESTS + "/" + identifier).status_code == 404
    assert member.get(TASKS + "/" + identifier).status_code == 404
    assert member.get(TASKS + "/" + identifier + "/logs").status_code == 404
    assert member.get(TASKS).json() == []
    with runtime.app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        task = session.get(AsyncTask, int(identifier))
        config = session.get(AIModelConfig, record.config_id)
        assert task.project_id is None and task.scope_user_id == int(user["id"])
        assert config.is_deleted and not config.enabled
        assert record.config_snapshot["canvas_model_test"]
        assert API_KEY not in json.dumps(record.request_data)
        assert HEADER_VALUE not in json.dumps(record.request_data)
        assert API_KEY not in json.dumps(record.config_snapshot)
        assert HEADER_VALUE not in json.dumps(record.config_snapshot)
        credentials = decode_canvas_credentials(
            record.config_snapshot,
            record.request_data,
            KeyCipher(runtime.app[2].encryption_key.get_secret_value()).decrypt(
                record.credential_cipher
            ),
        )
        assert credentials.api_key.get_secret_value() == API_KEY
        assert credentials.headers[0].name == HEADER_NAME
        assert credentials.headers[0].value.get_secret_value() == HEADER_VALUE
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0
        assert session.scalar(select(func.count()).select_from(CanvasTaskBinding)) == 0
        if kind != "text":
            media = session.scalar(select(MediaFile))
            assert media.project_id is None and media.scope_user_id == int(user["id"])
            assert media.published_at is None
    if kind == "text":
        assert completed["result"]["text"] == TEXT
        assert runtime.calls[0]["body"]["stream"] is False
    else:
        result = completed["result"]
        item = result["images"][0] if kind == "image" else result[kind]
        assert item["bytes"] == len(runtime.media[kind])
        assert owner.get(item["url"]).content == runtime.media[kind]
        partial = owner.get(item["url"], headers={"Range": "bytes=4-15"})
        assert partial.status_code == 206 and partial.content == runtime.media[kind][4:16]
        assert member.get(item["url"]).status_code == 404
        if kind == "image":
            assert (item["width"], item["height"]) == (37, 19)
        else:
            assert 900 <= item["durationMs"] <= 1500
            if kind == "video":
                assert (item["width"], item["height"], item["durationMs"]) == (160, 90, 1000)
                assert sum(call["path"] == "/movie.mp4" for call in runtime.calls) == 1
    assert owner.get("/api/v1/ai-model-configs").json()["items"] == []
