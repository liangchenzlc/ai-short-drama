"""画布音频经独立Celery进程、真实RabbitMQ/HTTP/MySQL/MinIO验证。"""

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from amqp.exceptions import NotFound as BrokerObjectMissing
from kombu import Queue
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, MediaFile
from short_drama.tasks.celery_app import make_celery, topology
from short_drama.tasks.runtime.publisher import Publisher
from tests.integration.canvas.audio.test_canvas_audio_protocols import (
    PROVIDER_ID,
    audio_seed,
    submissions,
)
from tests.integration.canvas.audio.test_canvas_audio_protocols import (
    audio_runtime as audio_runtime,
)
from tests.integration.canvas.generation.test_canvas_generation_broker import (
    eventually,
    poll_task,
    wait_for_consumers_to_detach,
    worker_environment,
)
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    admit,
    bind,
    records,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated real audio AMQP, Celery, HTTP and MinIO verification",
    ),
]


@pytest.fixture
def audio_broker(audio_runtime, tmp_path):
    runtime = audio_runtime
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    app, factory, current = runtime.app
    namespace = "canvas_audio_broker_test_" + uuid4().hex
    settings = current.model_copy(
        update={
            "generation_queue_namespace": namespace,
            "model_discovery_allowed_hosts": ["127.0.0.1"],
            "snowflake_worker_id": 1022,
        }
    )
    app.state.settings = settings
    runtime.app = app, factory, settings
    runtime.poll_conflicts = []
    celery = make_celery(settings)
    exchange, dead_exchange, queues = topology(settings)
    queue = queues["audio"]
    dead_queue = Queue(queue.name + ".dead", exchange=dead_exchange, routing_key=queue.routing_key)
    publisher = Publisher(factory, settings)
    stopped = threading.Event()
    errors = []
    worker = thread = None
    cleanup = {"namespace": namespace, "worker_stopped": False, "queues_absent": False}

    def publish():
        try:
            while not stopped.wait(0.02):
                publisher.tick()
        except BaseException as error:
            errors.append(type(error).__name__)

    def queue_state():
        assert worker.poll() is None, "Task-owned audio Celery process exited before delivery"
        with celery.connection_for_read() as connection:
            return connection.channel().queue_declare(queue.name, passive=True)

    try:
        with celery.connection_for_write() as connection:
            channel = connection.channel()
            queue(channel).declare()
            dead_queue(channel).declare()
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "short_drama.tasks.celery_app:app",
                "worker",
                "--pool=threads",
                "--concurrency=1",
                "--queues=" + queue.name,
                "--hostname=" + namespace + "@%h",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                "--loglevel=CRITICAL",
            ],
            cwd=Path(__file__).resolve().parents[4],
            env=worker_environment(settings, factory),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        eventually(queue_state, lambda value: value.consumer_count == 1, timeout=25)
        thread = threading.Thread(target=publish, name="canvas-audio-publisher", daemon=True)
        thread.start()
        yield runtime
        assert not errors, errors
        eventually(queue_state, lambda value: value.message_count == 0, timeout=10)
    finally:
        stopped.set()
        if thread is not None:
            thread.join(10)
            assert not thread.is_alive(), "Task-owned audio Publisher did not stop"
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
            cleanup["consumer_detach_wait_ms"] = wait_for_consumers_to_detach(celery, [queue])
            with celery.connection_for_write() as connection:
                channel = connection.channel()
                for item in (queue, dead_queue):
                    assert item.name.startswith(namespace + ".")
                    item(channel).delete(if_unused=True, if_empty=False)
                assert exchange.name == namespace + ".tasks"
                assert dead_exchange.name == namespace + ".dead"
                exchange(channel).delete(if_unused=True)
                dead_exchange(channel).delete(if_unused=True)
                for item in (queue, dead_queue):
                    check = connection.channel()
                    try:
                        with pytest.raises(BrokerObjectMissing):
                            check.queue_declare(item.name, passive=True)
                    finally:
                        check.close()
            cleanup["queues_absent"] = True
        finally:
            (tmp_path / "audio-broker-cleanup.json").write_text(
                json.dumps(cleanup, indent=2) + "\n", encoding="utf-8"
            )


@pytest.mark.parametrize("protocol", ["async-audio", "openai-audio", "unknown"])
def test_audio_tasks_cross_the_real_broker_and_keep_original_identity(audio_broker, protocol):
    runtime = audio_broker
    if protocol == "unknown":
        runtime.state["create"] = "lost-ack"
    client, user, project, path, request = audio_seed(
        runtime,
        protocol="openai-audio" if protocol == "openai-audio" else "async-audio",
        defaults={"instructions": "独立Worker的保存指令"},
    )
    request["input"]["config"]["audioInstructions"] = ""
    before = client.get(path).json()
    task = admit(client, request)
    completed = eventually(
        lambda: poll_task(runtime, client, task["id"]),
        lambda value: value is not None and value["status"] in {"succeeded", "failed"},
    )
    assert len(submissions(runtime)) == 1, json.dumps(completed, ensure_ascii=False)
    assert submissions(runtime)[0]["body"]["instructions"] == "独立Worker的保存指令"
    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    assert len(submissions(runtime)) == 1
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    if protocol == "unknown":
        assert completed["status"] == "failed" and completed.get("resultJson") is None
        assert not completed["canRetry"] and not completed["canResume"]
        assert client.post(f"{TASKS}/{task['id']}/resume").status_code == 409
        assert len(submissions(runtime)) == 1
        with runtime.app[1]() as session:
            assert session.scalar(select(AIGenerationRecord)).status == "unknown"
            assert session.scalar(select(MediaFile)) is None
        return
    assert completed["status"] == "succeeded"
    media = json.loads(completed["resultJson"])["audio"]
    assert client.get(media["url"]).content == runtime.media
    assert 900 <= media["durationMs"] <= 1500
    member, other = account(runtime.app, "audio_broker_member")
    assert member.get(media["url"]).status_code == 404
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    # 私人准入只更新作者节点的临时状态，不把成功作品直接写入共享图。
    assert client.get(path).json()["source_document"]["nodes"] == before["source_document"]["nodes"]
    bound = bind(client, request, completed)
    assert bound["result"]["node"]["metadata"]["storageKey"] == media["storageKey"]
    assert member.get(media["url"]).status_code == 404
    assert client.get(media["url"]).content == runtime.media
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    with runtime.app[1]() as session:
        call = session.scalar(select(AIGenerationRecord))
        assert call.provider_task_id == (PROVIDER_ID if protocol == "async-audio" else None)
        assert call.config_snapshot["canvas_audio_defaults"] == {
            "instructions": "独立Worker的保存指令"
        }
        file = session.scalar(select(MediaFile))
        assert file.checksum_sha256 == hashlib.sha256(runtime.media).hexdigest()
    assert len(submissions(runtime)) == 1
