"""独立Celery进程/AMQP上的非AI画布渲染；只创建并清理随机render队列。"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from amqp.exceptions import NotFound as BrokerObjectMissing
from sqlalchemy import select

from short_drama.domain import AsyncTask, CanvasRenderJob, MediaFile
from short_drama.tasks.canvas_render import CanvasRenderPublisher
from short_drama.tasks.celery_app import make_celery, render_queue
from tests.integration.canvas.generation.test_canvas_generation_broker import (
    eventually,
    wait_for_consumers_to_detach,
    worker_environment,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.timeline.test_canvas_render_execution import (
    PATH,
    detail,
    output_upload,
)
from tests.integration.canvas.timeline.test_canvas_render_execution import runtime as runtime
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_TIMELINE_FFMPEG") != "1",
        reason="Enable isolated real render AMQP/Celery/MySQL/MinIO/FFmpeg verification",
    ),
]


@pytest.fixture
def render_broker(runtime, tmp_path):
    namespace = "canvas_render_test_" + uuid4().hex
    assert re.fullmatch(r"canvas_render_test_[a-f0-9]{32}", namespace)
    settings = runtime[5].settings.model_copy(update={"generation_queue_namespace": namespace})
    runtime[6][0].state.settings = settings
    factory = runtime[6][1]
    publisher = CanvasRenderPublisher(factory, settings)
    celery = make_celery(settings)
    queue = render_queue(settings)
    errors, publications = [], []
    stopped, enabled = Event(), Event()
    enabled.set()
    worker, thread = None, None
    evidence = {"namespace": namespace, "publications": publications, "publisher_errors": errors}

    def queue_state():
        with celery.connection_for_read() as connection:
            return connection.channel().queue_declare(queue.name, passive=True)

    def publish():
        while not stopped.wait(0.05):
            if enabled.is_set():
                try:
                    if publisher.tick():
                        publications.append("published")
                except Exception as error:
                    errors.append(type(error).__name__)
                    stopped.set()

    log_path = tmp_path / "celery-render.log"
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
                cwd=Path(__file__).resolve().parents[4],
                env=worker_environment(settings, factory),
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        eventually(queue_state, lambda row: row.consumer_count == 1, timeout=30)
        thread = Thread(target=publish, name="canvas-render-test-publisher", daemon=True)
        thread.start()
        yield SimpleNamespace(
            runtime=runtime,
            settings=settings,
            publisher=publisher,
            queue=queue,
            celery=celery,
            enabled=enabled,
            worker=worker,
            evidence=evidence,
        )
        assert not errors, errors
        assert worker.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
        eventually(queue_state, lambda row: row.message_count == 0, timeout=15)
    finally:
        stopped.set()
        if thread is not None:
            thread.join(10)
            assert not thread.is_alive(), "Own render Publisher did not stop"
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
        evidence["queue_absent"] = True
        (tmp_path / "render-broker.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def test_non_ai_render_crosses_real_broker_and_subprocess_worker_once(render_broker):
    broker = render_broker
    runtime = broker.runtime
    state = eventually(
        lambda: detail(runtime), lambda row: row["status"] in {"succeeded", "failed"}
    )
    assert state["status"] == "succeeded", state
    assert output_upload(runtime).status == "ready"
    result = json.loads(state["resultJson"])
    content = runtime[0].get(PATH + f"/resources/{result['resourceId']}/file")
    assert content.status_code == 200 and len(content.content) == result["size"]
    for version in ("1", "0"):
        broker.celery.send_task(
            "short_drama.execute_canvas_render",
            args=[runtime[4]["id"], version],
            queue=broker.queue.name,
            routing_key="render",
            retry=False,
        )
    eventually(lambda: detail(runtime), lambda row: row == state)
    with runtime[6][1]() as session:
        job = session.get(CanvasRenderJob, int(runtime[4]["id"]))
        assert job.attempts == 1 and str(job.output_media_id) == result["resourceId"]
        assert list(session.scalars(select(AsyncTask))) == []
        assert len(list(session.scalars(select(MediaFile)))) == 2
    broker.evidence.update(
        worker_pid=broker.worker.pid,
        task_id=runtime[4]["id"],
        actual_size=len(content.content),
        duplicate_delivery_attempts=job.attempts,
    )
