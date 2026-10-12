"""两套 RunningHub 经独立 Celery、真实 RabbitMQ/HTTP/MySQL/MinIO 执行。"""

import hashlib
import json
import os
import subprocess
import sys
import threading
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from amqp.exceptions import NotFound as BrokerObjectMissing
from kombu import Queue
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, MediaFile
from short_drama.tasks.celery_app import make_celery, topology
from short_drama.tasks.publisher import Publisher
from tests.integration.test_canvas_generation_broker import (
    eventually,
    poll_task,
    wait_for_consumers_to_detach,
    worker_environment,
)
from tests.integration.test_canvas_generation_runtime import TASKS, admit, bind, records
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_runninghub_declarative import (
    OPTIONS,
    POLL,
    PROVIDER_ID,
    declarative_seed,
)
from tests.integration.test_canvas_runninghub_declarative import creates as declarative_creates
from tests.integration.test_canvas_runninghub_declarative import (
    declarative_runtime as declarative_runtime,
)
from tests.integration.test_canvas_runninghub_runtime import TASK_ID, runninghub_seed
from tests.integration.test_canvas_runninghub_runtime import creates as builtin_creates
from tests.integration.test_canvas_runninghub_runtime import (
    runninghub_runtime as runninghub_runtime,
)
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated RunningHub AMQP, subprocess Celery, HTTP and MinIO verification",
    ),
]


@pytest.fixture
def runninghub_broker(request, tmp_path):
    case = request.param
    declarative = case.startswith("declarative")
    runtime = request.getfixturevalue(
        "declarative_runtime" if declarative else "runninghub_runtime"
    )
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    app, factory, current = runtime.app
    namespace = "canvas_runninghub_broker_test_" + uuid4().hex
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
    own_queues = [queues[kind] for kind in ("image", "video", "audio")]
    dead_queues = [
        Queue(
            item.name + ".dead", exchange=dead_exchange, routing_key=item.routing_key, durable=True
        )
        for item in own_queues
    ]
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
        assert worker.poll() is None, "Task-owned RunningHub Celery process exited before delivery"
        with celery.connection_for_read() as connection:
            channel = connection.channel()
            return [channel.queue_declare(item.name, passive=True) for item in own_queues]

    try:
        with celery.connection_for_write() as connection:
            channel = connection.channel()
            for item in [*own_queues, *dead_queues]:
                item(channel).declare()
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
                "--queues=" + ",".join(item.name for item in own_queues),
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
            queue_state, lambda values: all(item.consumer_count == 1 for item in values), timeout=25
        )
        thread = threading.Thread(target=publish, name="canvas-runninghub-publisher", daemon=True)
        thread.start()
        yield runtime, case
        assert not errors, errors
        eventually(
            queue_state, lambda values: all(item.message_count == 0 for item in values), timeout=10
        )
    finally:
        stopped.set()
        if thread is not None:
            thread.join(10)
            assert not thread.is_alive(), "Task-owned RunningHub Publisher did not stop"
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(10)
        cleanup["worker_stopped"] = worker is None or worker.poll() is not None
        cleanup["poll_conflicts"] = runtime.poll_conflicts
        try:
            assert cleanup["worker_stopped"]
            cleanup["consumer_detach_wait_ms"] = wait_for_consumers_to_detach(celery, own_queues)
            with celery.connection_for_write() as connection:
                channel = connection.channel()
                for item in [*own_queues, *dead_queues]:
                    assert item.name.startswith(namespace + ".")
                    item(channel).delete(if_unused=True, if_empty=False)
                assert exchange.name == namespace + ".tasks"
                assert dead_exchange.name == namespace + ".dead"
                exchange(channel).delete(if_unused=True)
                dead_exchange(channel).delete(if_unused=True)
                for item in [*own_queues, *dead_queues]:
                    check = connection.channel()
                    try:
                        with pytest.raises(BrokerObjectMissing):
                            check.queue_declare(item.name, passive=True)
                    finally:
                        check.close()
            cleanup["queues_absent"] = True
        finally:
            (tmp_path / "runninghub-broker-cleanup.json").write_text(
                json.dumps(cleanup, indent=2) + "\n", encoding="utf-8"
            )


@pytest.mark.parametrize(
    "runninghub_broker",
    [
        "workflow-image",
        "app-video",
        "workflow-audio",
        "declarative-video",
        "builtin-unknown",
        "declarative-unknown",
    ],
    indirect=True,
)
def test_runninghub_real_delivery_preserves_each_protocol_and_original_identity(runninghub_broker):
    runtime, case = runninghub_broker
    declarative = case.startswith("declarative")
    unknown = case.endswith("unknown")
    if unknown:
        runtime.state["create"] = "disconnect"
    if declarative:
        mode = "video"
        client, user, project, path, request = declarative_seed(runtime)
        request["input"]["metadata"]["providerOptions"] = {"runninghub-workflow": deepcopy(OPTIONS)}
        creates = declarative_creates
        media_bytes = runtime.media
    else:
        mode = "image" if unknown else case.split("-")[1]
        client, user, project, path, request = runninghub_seed(
            runtime, mode=mode, kind="app" if case.startswith("app") else "workflow"
        )
        creates = builtin_creates
        media_bytes = runtime.media[mode]
    before = client.get(path).json()["source_document"]["nodes"]
    task = admit(client, request)
    completed = eventually(
        lambda: poll_task(runtime, client, task["id"]),
        lambda value: value is not None and value["status"] in {"succeeded", "failed"},
        timeout=60,
    )
    assert len(creates(runtime)) == 1, json.dumps(completed, ensure_ascii=False)
    replay = client.post(TASKS, json=request)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    if unknown:
        assert completed["status"] == "failed" and completed.get("resultJson") is None
        assert not completed["canRetry"] and not completed["canResume"]
        assert client.post(f"{TASKS}/{task['id']}/resume").status_code == 409
        with runtime.app[1]() as session:
            assert session.scalar(select(AIGenerationRecord)).status == "unknown"
            assert session.scalar(select(MediaFile)) is None
        assert len(creates(runtime)) == 1
        return
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    result = json.loads(completed["resultJson"])
    media = result["images"][0] if mode == "image" else result[mode]
    assert media["storageKey"].startswith("resource:")
    assert client.get(media["url"]).content == media_bytes
    assert client.get(path).json()["source_document"]["nodes"] == before
    member, other = account(runtime.app, "runninghub_broker_member")
    join(runtime.app, client, member, project["id"], other["id"])
    assert (
        member.get(media["url"]).status_code
        == member.get(f"{TASKS}/{task['id']}").status_code
        == 404
    )
    bound = bind(client, request, completed)
    assert bound["result"]["node"]["metadata"]["storageKey"] == media["storageKey"]
    assert member.get(media["url"]).content == media_bytes
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    if declarative:
        polls = [call for call in runtime.calls if call["path"] == POLL]
        assert len(polls) == 1 and polls[0]["method"] == "GET" and polls[0]["body"] is None
        expected = {
            **{key: value for key, value in OPTIONS.items() if key != "workflow"},
            "prompt": OPTIONS["workflow"],
        }
        assert creates(runtime)[0]["body"] == expected
    else:
        polls = [call for call in runtime.calls if call["path"] == "/task/openapi/outputs"]
        assert (
            len(polls) == 1
            and polls[0]["method"] == "POST"
            and polls[0]["body"]["taskId"] == TASK_ID
        )
    with runtime.app[1]() as session:
        call = session.scalar(select(AIGenerationRecord))
        assert call.provider_task_id == (PROVIDER_ID if declarative else TASK_ID)
        file = session.scalar(select(MediaFile))
        assert file.checksum_sha256 == hashlib.sha256(media_bytes).hexdigest()
    assert len(creates(runtime)) == 1
