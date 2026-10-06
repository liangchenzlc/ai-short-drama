"""源画布经过独立 Celery 进程、真实 AMQP/HTTP/MySQL/MinIO 的隔离验证。"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from amqp.exceptions import NotFound as BrokerObjectMissing
from kombu import Queue
from pydantic import SecretStr
from sqlalchemy import select

from short_drama.domain import AsyncTask, MediaFile
from short_drama.tasks.celery_app import make_celery, topology
from short_drama.tasks.publisher import Publisher
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_generation_runtime import TASKS, admit, bind, seed
from tests.integration.test_canvas_resources import png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated real AMQP, subprocess Celery, HTTP and MinIO verification",
    ),
]
TEXT = "浏览器刷新后恢复的真实任务正文"


def eventually(read, accepted, *, timeout=40):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = read()
        if accepted(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"Isolated runtime did not reach its expected state: {last}")


def wait_for_consumers_to_detach(celery, queues):
    """已停自建 Worker 后，等待 broker 完成该随机 namespace 的消费者取消。"""
    started = time.monotonic()

    def current():
        with celery.connection_for_read() as connection:
            channel = connection.channel()
            return [channel.queue_declare(queue.name, passive=True) for queue in queues]

    eventually(current, lambda rows: all(row.consumer_count == 0 for row in rows), timeout=10)
    return round((time.monotonic() - started) * 1000)


def poll_task(runtime, client, identifier):
    """仅瞬态数据库冲突可重复同一 GET；生成 POST 从不重发。"""
    path = f"{TASKS}/{identifier}"
    response = client.get(path)
    if response.status_code == 409:
        assert response.json() == {
            "error": {
                "code": "conflict",
                "message": "Concurrent operation conflict; retry operation",
            }
        }, response.text
        runtime.poll_conflicts.append({"path": path, "task_id": identifier, "code": "conflict"})
        return None
    assert response.status_code == 200, response.text
    return response.json()


def worker_environment(settings, factory):
    """仅内存传递 Settings；数据库必须来自 fixture，不能沿用业务库。"""
    environment = dict(os.environ)
    for name, value in settings.model_dump().items():
        if value is None:
            environment.pop(name.upper(), None)
        elif isinstance(value, SecretStr):
            environment[name.upper()] = value.get_secret_value()
        elif isinstance(value, (list, dict)):
            environment[name.upper()] = json.dumps(value)
        else:
            environment[name.upper()] = str(value)
    with factory() as session:
        url = session.get_bind().url
    assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", url.database or "")
    environment.update(
        DB_HOST=url.host,
        DB_PORT=str(url.port),
        DB_USER=url.username,
        DB_PASSWORD=url.password or "",
        DB_NAME=url.database,
        SNOWFLAKE_WORKER_ID="1023",
        PYTHONUNBUFFERED="1",
    )
    return environment


@pytest.fixture
def broker_app(resource_app, tmp_path):
    app, factory, current = resource_app
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022", "Reserve distinct test process IDs"
    namespace = "canvas_broker_test_" + uuid4().hex
    assert re.fullmatch(r"canvas_broker_test_[a-f0-9]{32}", namespace)
    settings = current.model_copy(
        update={
            "generation_queue_namespace": namespace,
            "model_discovery_allowed_hosts": ["127.0.0.1"],
            "snowflake_worker_id": 1022,
        }
    )
    app.state.settings = settings
    released = threading.Event()
    stopped = threading.Event()
    release_path = tmp_path / "source-browser-refreshed"
    posts, supplier_errors, publisher_errors = [], [], []
    image = png("purple")

    class Supplier(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.headers.get_content_type() == "multipart/form-data":
                    message = BytesParser(policy=policy.default).parsebytes(
                        f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw
                    )
                    body = [
                        (
                            part.get_param("name", header="content-disposition"),
                            part.get_filename(),
                            part.get_content_type(),
                            part.get_payload(decode=True),
                        )
                        for part in message.iter_parts()
                    ]
                else:
                    body = json.loads(raw)
                posts.append((self.path, body))
                if self.path == "/v1/chat/completions":
                    assert body["stream"] is True
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.chunk({"choices": [{"delta": {"content": "浏览器刷新后恢复的"}}]})
                    deadline = time.monotonic() + 90
                    while not released.is_set() and not release_path.exists():
                        if stopped.wait(0.02) or time.monotonic() >= deadline:
                            raise AssertionError("Source browser did not release HTTP stream")
                    self.chunk({"choices": [{"delta": {"content": "真实任务正文"}}]})
                    self.chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                    self.chunk("[DONE]")
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                elif self.path in {"/v1/images/generations", "/v1/images/edits"}:
                    payload = json.dumps(
                        {"data": [{"b64_json": base64.b64encode(image).decode()}]}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_error(404)
            except BaseException as error:
                # 不保存请求 headers 或 Authorization，也不输出供应商凭据。
                supplier_errors.append(type(error).__name__)
                self.close_connection = True

        def chunk(self, payload):
            value = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            content = ("data: " + value + "\n\n").encode()
            self.wfile.write(f"{len(content):x}\r\n".encode() + content + b"\r\n")
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    supplier = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    supplier_thread = threading.Thread(target=supplier.serve_forever, daemon=True)
    supplier_thread.start()
    celery = make_celery(settings)
    exchange, dead_exchange, queues = topology(settings)
    own_queues = [queues[kind] for kind in ("text", "image")]
    publisher = Publisher(factory, settings)
    worker = None
    publication_thread = None
    cleanup = {
        "namespace": namespace,
        "worker_stopped": False,
        "queues_absent": False,
        "poll_conflicts": [],
    }

    def publish():
        try:
            while not stopped.wait(0.02):
                publisher.tick()
        except BaseException as error:
            publisher_errors.append(type(error).__name__)

    def queue_state():
        assert worker.poll() is None, "Task-owned Celery process exited before delivery"
        with celery.connection_for_read() as connection:
            channel = connection.channel()
            return [channel.queue_declare(queue.name, passive=True) for queue in own_queues]

    try:
        with celery.connection_for_write() as connection:
            channel = connection.channel()
            for queue in own_queues:
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
                "--queues=" + ",".join(queue.name for queue in own_queues),
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
            url=f"http://127.0.0.1:{supplier.server_port}/v1",
            release=released,
            release_path=release_path,
            posts=posts,
            image=image,
            worker_pid=worker.pid,
            poll_conflicts=cleanup["poll_conflicts"],
        )
        assert not supplier_errors, supplier_errors
        assert not publisher_errors, publisher_errors
        eventually(
            queue_state, lambda rows: all(row.message_count == 0 for row in rows), timeout=10
        )
    finally:
        released.set()
        stopped.set()
        if publication_thread is not None:
            publication_thread.join(10)
            assert not publication_thread.is_alive(), "Task-owned Publisher did not stop"
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
            cleanup["consumer_detach_wait_ms"] = wait_for_consumers_to_detach(celery, own_queues)
            # 仅删除精确 UUID namespace 创建的两个队列及其 dead 队列。
            with celery.connection_for_write() as connection:
                channel = connection.channel()
                for queue in own_queues:
                    assert queue.name.startswith(namespace + ".")
                    queue(channel).delete(if_unused=True, if_empty=False)
                    Queue(queue.name + ".dead", channel=channel).delete(
                        if_unused=True, if_empty=False
                    )
                assert exchange.name == namespace + ".tasks"
                assert dead_exchange.name == namespace + ".dead"
                exchange(channel).delete(if_unused=True)
                dead_exchange(channel).delete(if_unused=True)
                for name in [
                    value for queue in own_queues for value in (queue.name, queue.name + ".dead")
                ]:
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
            assert not supplier_thread.is_alive(), "Task-owned HTTP supplier did not stop"
            cleanup["http_supplier_stopped"] = True
            evidence = Path(__file__).resolve().parents[3] / ".runtime" / "canvas-broker-runtime"
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / (namespace + ".json")).write_text(
                json.dumps(cleanup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )


@pytest.mark.parametrize("kind", ["text", "image"])
def test_source_task_crosses_subprocess_broker_http_and_shared_bind(broker_app, kind):
    runtime = broker_app
    client, user, project, path, request = seed(runtime.app, "canvas_broker_" + kind, kind=kind)
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    original = client.get(model_path)
    assert original.status_code == 200, original.text
    model = client.patch(
        model_path,
        json={
            "row_version": original.json()["row_version"],
            "base_url": runtime.url,
            "apikey": "canvas-broker-placeholder-not-a-real-key",
        },
    )
    assert model.status_code == 200, model.text
    request["input"]["textOptions"]["stream"] = True
    member, other = account(runtime.app, "canvas_broker_reader_" + kind)
    join(runtime.app, client, member, project["id"], other["id"])
    member.headers["X-Canvas-Actor"] = other["id"]
    task = admit(client, request)

    def read():
        return poll_task(runtime, client, task["id"])

    if kind == "text":
        partial = eventually(read, lambda row: row is not None and bool(row.get("textDraft")))
        assert partial["textDraft"] == "浏览器刷新后恢复的"
        assert partial["status"] == "running" and partial["resultState"] != "READY"
        assert member.get(f"{TASKS}/{task['id']}").status_code == 404
        runtime.release.set()
    ready = eventually(
        read, lambda row: row is not None and row["status"] in {"succeeded", "failed"}
    )
    assert ready["status"] == "succeeded" and ready["resultState"] == "READY", ready
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    if kind == "text":
        assert ready["textDraft"] == TEXT
        assert TEXT not in json.dumps(member.get(path).json(), ensure_ascii=False)
    else:
        media = json.loads(ready["resultJson"])["images"][0]
        assert (media["width"], media["height"], media["bytes"]) == (37, 19, len(runtime.image))
        assert client.get(media["url"]).content == runtime.image
        assert member.get(media["url"]).status_code == 404
        with runtime.app[1]() as session:
            stored = session.scalar(select(MediaFile))
            assert stored.storage_locator.startswith("minio://canvas-test-")
    bound = bind(client, request, ready)
    shared = member.get(path).json()["source_document"]["nodes"][0]
    if kind == "text":
        assert bound["result"]["node"]["metadata"]["content"] == TEXT
        assert shared["metadata"]["content"] == TEXT
    else:
        assert shared["metadata"]["storageKey"] == media["storageKey"]
        assert member.get(media["url"]).content == runtime.image
    with runtime.app[1]() as session:
        durable = session.get(AsyncTask, int(task["id"]))
        assert durable.status == "succeeded" and durable.message_status == "idle"
    assert len(runtime.posts) == 1, "The real supplier POST must occur exactly once"


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1", reason="Enable real source browser"
)
def test_original_text_and_image_ui_use_real_broker_and_http(broker_app):
    runtime = broker_app
    node = shutil.which("node")
    assert node
    client, _ = account(runtime.app, "canvas_broker_browser")
    assert client.post("/api/v1/auth/logout").status_code == 200
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4188"})
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "username": "canvas_broker_browser",
            "password": PASSWORD,
            "supplierUrl": runtime.url,
            "releasePath": str(runtime.release_path),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-broker-generation-python.mjs"],
            cwd=frontend,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Broker source browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Broker source browser failed: {errors}\n{output}"
        result = json.loads(output)
        assert result["source_saved_before_admission"]
        assert result["same_task_recovered_after_refresh"]
        assert result["source_bind_applied"] and result["persisted_after_fresh_browser"]
        assert result["image_bound_and_loaded"] and result["image_loaded_after_fresh_browser"]
        assert result["task_post_count"] == len(runtime.posts) == 2
        assert not result["page_errors"]
