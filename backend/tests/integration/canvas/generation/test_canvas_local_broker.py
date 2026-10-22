"""独立 Celery/AMQP 私人转写；受控 HTTP，不运行实际 ASR 模型。"""

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

from short_drama.domain import AsyncTask, CanvasLocalJob, MediaFile
from short_drama.tasks.canvas_local import CanvasLocalPublisher
from short_drama.tasks.celery_app import make_celery, render_queue
from tests.integration.canvas.audio.test_canvas_transcription_wav_http import whisper_server
from tests.integration.canvas.generation.test_canvas_generation_broker import (
    eventually,
    wait_for_consumers_to_detach,
    worker_environment,
)
from tests.integration.canvas.generation.test_canvas_local_execution import PATH, detail
from tests.integration.canvas.generation.test_canvas_local_execution import runtime as runtime
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_TRANSCRIPTION_FFMPEG") != "1",
        reason="Enable isolated real transcription AMQP/Celery/MySQL/MinIO/FFmpeg verification",
    ),
]


@pytest.fixture(params=["success", "service_error"])
def local_broker(runtime, tmp_path, request):
    namespace = "canvas_local_test_" + uuid4().hex
    assert re.fullmatch(r"canvas_local_test_[a-f0-9]{32}", namespace)
    failed = request.param == "service_error"
    with whisper_server(
        status=503 if failed else 200, payload=b"controlled whisper unavailable" if failed else None
    ) as (url, received):
        settings = runtime[5].settings.model_copy(
            update={"generation_queue_namespace": namespace, "canvas_whisper_base_url": url}
        )
        runtime[6][0].state.settings = settings
        publisher = CanvasLocalPublisher(runtime[6][1], settings)
        celery, queue = make_celery(settings), render_queue(settings)
        errors, publications = [], []
        stopped = Event()
        worker, thread = None, None
        evidence = {
            "namespace": namespace,
            "publications": publications,
            "publisher_errors": errors,
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

        log_path = tmp_path / "celery-local.log"
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
                    env=worker_environment(settings, runtime[6][1]),
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            eventually(queue_state, lambda row: row.consumer_count == 1, timeout=30)
            thread = Thread(target=publish, name="canvas-local-test-publisher", daemon=True)
            thread.start()
            yield SimpleNamespace(
                runtime=runtime,
                queue=queue,
                celery=celery,
                worker=worker,
                evidence=evidence,
                received=received,
                failed=failed,
            )
            assert not errors, errors
            assert worker.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
            eventually(queue_state, lambda row: row.message_count == 0, timeout=15)
        finally:
            stopped.set()
            if thread is not None:
                thread.join(10)
                assert not thread.is_alive(), "Own local Publisher did not stop"
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
            evidence.update(queue_absent=True, service_posts=len(received))
            (tmp_path / "local-broker.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )


def test_transcription_crosses_real_broker_and_subprocess_worker_once(local_broker):
    broker, runtime = local_broker, local_broker.runtime
    state = eventually(
        lambda: detail(runtime), lambda row: row["status"] in {"succeeded", "failed"}
    )
    assert len(broker.received) == 1
    assert state["status"] == ("failed" if broker.failed else "succeeded"), state
    if broker.failed:
        assert "返回 503" in state["error"] and state["canResume"] and "resultJson" not in state
    else:
        assert json.loads(state["resultJson"])["segments"] == [
            {"startMs": 1, "endMs": 350, "text": "中文语音"}
        ]
    assert runtime[0].get(PATH + f"/resources/{runtime[3]['id']}/file").content == runtime[7]
    for version in ("1", "0"):
        broker.celery.send_task(
            "short_drama.execute_canvas_local",
            args=[runtime[4]["id"], version],
            queue=broker.queue.name,
            routing_key="render",
            retry=False,
        )
    eventually(lambda: detail(runtime), lambda row: row == state)
    with runtime[6][1]() as session:
        job = session.get(CanvasLocalJob, int(runtime[4]["id"]))
        assert job.attempts == 1 and job.output_media_id is None
        assert list(session.scalars(select(AsyncTask))) == []
        assert len(list(session.scalars(select(MediaFile)))) == 1
    broker.evidence.update(
        worker_pid=broker.worker.pid,
        task_id=runtime[4]["id"],
        duplicate_delivery_attempts=job.attempts,
    )
