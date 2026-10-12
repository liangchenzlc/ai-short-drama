"""深度私人任务经独立 AMQP/Worker 运行；组件替身只生成真实灰度视频。"""

import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from amqp.exceptions import NotFound as BrokerObjectMissing
from sqlalchemy import select

from short_drama.domain import AsyncTask, CanvasLocalJob, MediaFile
from short_drama.tasks.canvas_local import CanvasLocalPublisher
from short_drama.tasks.celery_app import make_celery, render_queue
from tests.integration.test_canvas_depth_execution import PATH, calls, create, detail
from tests.integration.test_canvas_depth_execution import depth_runtime as depth_runtime
from tests.integration.test_canvas_generation_broker import (
    eventually,
    wait_for_consumers_to_detach,
    worker_environment,
)
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_DEPTH_NATIVE") != "1",
        reason="Enable isolated depth process/AMQP/MySQL/MinIO/FFmpeg checks",
    ),
]


@contextmanager
def independent_depth_worker(runtime, tmp_path):
    namespace = "canvas_depth_test_" + uuid4().hex
    assert re.fullmatch(r"canvas_depth_test_[a-f0-9]{32}", namespace)
    settings = runtime.executor.settings.model_copy(
        update={"generation_queue_namespace": namespace}
    )
    runtime.app[0].state.settings = settings
    publisher = CanvasLocalPublisher(runtime.app[1], settings)
    celery, queue = make_celery(settings), render_queue(settings)
    stopped = Event()
    errors, publications = [], []
    worker, thread = None, None
    evidence = {
        "namespace": namespace,
        "publisher_errors": errors,
        "publications": publications,
        "component_is_test_double": getattr(runtime, "component_is_test_double", True),
    }

    def queue_state():
        with celery.connection_for_read() as connection:
            return connection.channel().queue_declare(queue.name, passive=True)

    def publish():
        while not stopped.wait(0.05):
            try:
                if publisher.tick():
                    publications.append("published")
            except Exception as error:
                errors.append(type(error).__name__)
                stopped.set()

    log_path = tmp_path / "celery-depth.log"
    try:
        with celery.connection_for_write() as connection:
            queue(connection.channel()).declare()
        with log_path.open("wb") as log:
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
                    "--queues=" + queue.name,
                    "--hostname=" + namespace + "@%h",
                    "--without-gossip",
                    "--without-mingle",
                    "--without-heartbeat",
                    "--loglevel=WARNING",
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=worker_environment(settings, runtime.app[1]),
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        eventually(queue_state, lambda row: row.consumer_count == 1, timeout=30)
        thread = Thread(target=publish, name="canvas-depth-test-publisher", daemon=True)
        thread.start()
        yield SimpleNamespace(
            runtime=runtime,
            task=None,
            worker=worker,
            celery=celery,
            queue=queue,
            failed=False,
            evidence=evidence,
        )
        assert not errors, errors
        assert worker.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
        eventually(queue_state, lambda row: row.message_count == 0, timeout=15)
    finally:
        stopped.set()
        if thread is not None:
            thread.join(10)
            assert not thread.is_alive(), "Own depth Publisher did not stop"
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(10)
        evidence["worker_stopped"] = worker is None or worker.poll() is not None
        evidence["consumer_detach_wait_ms"] = wait_for_consumers_to_detach(celery, [queue])
        with celery.connection_for_write() as connection:
            channel = connection.channel()
            assert queue.name == namespace + ".tasks.render"
            queue(channel).delete(if_unused=True, if_empty=False)
            assert queue.exchange.name == namespace + ".tasks"
            queue.exchange(channel).delete(if_unused=True)
            check = connection.channel()
            try:
                with pytest.raises(BrokerObjectMissing):
                    check.queue_declare(queue.name, passive=True)
            finally:
                check.close()
        evidence.update(queue_absent=True, component_calls=len(calls(runtime)))
        (tmp_path / "depth-broker.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@pytest.fixture(params=["success", "failure"])
def depth_broker(depth_runtime, tmp_path, request):
    (depth_runtime.component / "mode").write_text(request.param)
    with independent_depth_worker(depth_runtime, tmp_path) as broker:
        broker.task = create(depth_runtime)
        broker.failed = request.param == "failure"
        yield broker


def test_depth_crosses_independent_broker_once_and_archives_private_result(depth_broker):
    broker, runtime, task = depth_broker, depth_broker.runtime, depth_broker.task
    state = eventually(
        lambda: detail(runtime, task),
        lambda row: row["status"] in {"succeeded", "failed"},
        timeout=45,
    )
    assert state["status"] == ("failed" if broker.failed else "succeeded"), state
    assert len(calls(runtime)) == 1
    if broker.failed:
        assert state["canResume"] and "controlled component failure" in state["error"]
    else:
        result = json.loads(state["resultJson"])
        response = runtime.owner.get(PATH + f"/resources/{result['resourceId']}/file")
        assert response.status_code == 200 and len(response.content) == result["size"]
    for version in ("1", "0"):
        broker.celery.send_task(
            "short_drama.execute_canvas_local",
            args=[task["id"], version],
            queue=broker.queue.name,
            routing_key="render",
            retry=False,
        )
    eventually(lambda: detail(runtime, task), lambda row: row == state)
    with runtime.app[1]() as session:
        job = session.get(CanvasLocalJob, int(task["id"]))
        assert job.attempts == 1 and bool(job.output_media_id) is not broker.failed
        assert list(session.scalars(select(AsyncTask))) == []
        assert len(list(session.scalars(select(MediaFile)))) == (1 if broker.failed else 2)
    broker.evidence.update(
        worker_pid=broker.worker.pid, task_id=task["id"], duplicate_delivery_attempts=job.attempts
    )
