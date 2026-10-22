"""Celery --include 专用观测：生成任务和执行器仍使用正式实现。"""

import json
import os
import threading
from pathlib import Path

from celery.signals import task_postrun, task_prerun

from short_drama.tasks import worker
from tests.integration.canvas.assistant.test_canvas_agnes_live import LiveGateway

directory = Path(os.environ["AGNES_GENERATION_BROKER_DIRECTORY"]).resolve()
assert directory.is_dir()
lock = threading.Lock()
deliveries = []


def checkpoint():
    (directory / "generation-worker-report.json").write_text(
        json.dumps(
            {"pid": os.getpid(), "parent_pid": os.getppid(), "deliveries": deliveries}, indent=2
        ),
        encoding="utf-8",
    )


def started(sender=None, task_id=None, task=None, args=None, **kwargs):
    if sender.name != "short_drama.execute_generation":
        return
    with lock:
        deliveries.append(
            {
                "celery_task_id": task_id,
                "task_id": args[0],
                "message_version": args[1],
                "delivery": task.request.delivery_info,
                "completed": False,
            }
        )
        checkpoint()


def finished(sender=None, task_id=None, state=None, **kwargs):
    if sender.name != "short_drama.execute_generation":
        return
    with lock:
        row = next(row for row in deliveries if row["celery_task_id"] == task_id)
        row.update(completed=True, state=state)
        checkpoint()


class ObservedGateway(LiveGateway):
    def __init__(self, settings):
        super().__init__(
            settings, directory / "real-provider-submit-allowed", threading.Event(), directory
        )
        self.report.update(worker_pid=os.getpid(), snowflake_worker_id=settings.snowflake_worker_id)
        self.checkpoint()


worker.GenerationGateway = ObservedGateway
task_prerun.connect(started, weak=False)
task_postrun.connect(finished, weak=False)
