"""正式 Agent Celery 入口的测试观测；不替换任务、执行器、发布或恢复实现。"""

import json
import os
import sys
import threading
from pathlib import Path


def main():
    from celery.signals import task_postrun, task_prerun

    from short_drama.tasks import agent_worker
    from short_drama.tasks.celery_app import app
    from tests.integration.test_project_assistant_agnes_live import (
        LIFECYCLE_MODES,
        LiveAssistantGateway,
        LiveLifecycleGateway,
    )

    directory = Path(sys.argv[1]).resolve()
    assert directory.is_dir()
    lock = threading.Lock()
    mode = os.environ.get("AGNES_ASSISTANT_BROKER_LIFECYCLE_MODE")
    assert mode is None or mode in LIFECYCLE_MODES
    previous = {}
    saved = directory / "worker-report.json"
    if mode and saved.exists():
        previous = json.loads(saved.read_text(encoding="utf-8"))
    deliveries = previous.get("deliveries", [])
    for row in deliveries:
        if not row["completed"]:
            row["worker_lost"] = True
    executions = [
        *previous.get("executions", []),
        {"pid": os.getpid(), "parent_pid": os.getppid()},
    ]

    def checkpoint():
        (directory / "worker-report.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "parent_pid": os.getppid(),
                    "executions": executions,
                    "deliveries": deliveries,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def started(sender=None, task_id=None, task=None, args=None, **kwargs):
        if sender.name != "short_drama.execute_agent":
            return
        with lock:
            deliveries.append(
                {
                    "task_id": task_id,
                    "run_id": args[0],
                    "message_version": args[1],
                    "delivery": task.request.delivery_info,
                    "worker_pid": os.getpid(),
                    "parent_pid": os.getppid(),
                    "completed": False,
                }
            )
            checkpoint()

    def finished(sender=None, task_id=None, state=None, **kwargs):
        if sender.name != "short_drama.execute_agent":
            return
        with lock:
            row = next(
                row
                for row in reversed(deliveries)
                if row["task_id"] == task_id and row["worker_pid"] == os.getpid()
            )
            row.update(completed=True, state=state)
            checkpoint()

    gateway_type = LiveLifecycleGateway if mode else LiveAssistantGateway

    class ObservedGateway(gateway_type):
        def __init__(self, settings):
            if mode:
                super().__init__(settings, directory, mode)
                self.report.setdefault("original_worker_pid", os.getpid())
            else:
                super().__init__(settings, directory, threading.Event())
            self.report.update(
                worker_pid=os.getpid(), snowflake_worker_id=settings.snowflake_worker_id
            )
            self.checkpoint()

    task_prerun.connect(started, weak=False)
    task_postrun.connect(finished, weak=False)
    agent_worker.AgentModelGateway = ObservedGateway
    checkpoint()
    app.worker_main(sys.argv[2:])


if __name__ == "__main__":
    main()
