"""真实基础设施验收共用的隔离启动器；导入本模块不会触发供应商调用。"""

import json
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from amqp.exceptions import NotFound as BrokerObjectMissing
from kombu import Queue

from short_drama.tasks.celery_app import agent_queue, make_celery, render_queue, topology
from tests.integration.canvas.document.test_canvas_browser import stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import (
    eventually,
    wait_for_consumers_to_detach,
    worker_environment,
)

ROOT = Path(__file__).resolve().parents[4]
ROLE_IDS = dict(
    zip(
        ("api", "scheduler", "text", "image", "video", "audio", "render", "agent"),
        range(980, 988),
        strict=True,
    )
)


def read_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


@contextmanager
def official_stack(factory, settings, directory, *, generation_observer_module=None):
    """每个进程读取同版源码和内存环境；不覆盖用户 PID、日志、配置或队列。"""
    from urllib.error import URLError
    from urllib.request import urlopen

    assert settings.auth_enabled and settings.agent_enabled
    assert not settings.smtp_host and not settings.smtp_from
    assert len(set(ROLE_IDS.values())) == 8
    assert settings.snowflake_worker_id not in ROLE_IDS.values()
    celery = make_celery(settings)
    exchange, dead_exchange, generation = topology(settings)
    queues = {**generation, "render": render_queue(settings), "agent": agent_queue(settings)}
    dead_queues = [
        Queue(queue.name + ".dead", exchange=dead_exchange, routing_key=role, durable=True)
        for role, queue in queues.items()
        if role != "render"
    ]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        api_port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{api_port}"
    processes, logs = {}, []
    report = {
        "scope": "isolated formal eight-role runtime",
        "namespace": settings.generation_queue_namespace,
        "api_port": api_port,
        "roles": {},
        "smtp_disabled": True,
        "cleanup": {},
    }

    def checkpoint():
        (directory / "stack-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    def start(role, arguments):
        environment = worker_environment(settings, factory)
        environment["SNOWFLAKE_WORKER_ID"] = str(ROLE_IDS[role])
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["SMTP_HOST"] = environment["SMTP_FROM"] = ""
        output = (directory / f"{role}.log").open("w", encoding="utf-8")
        logs.append(output)
        process = subprocess.Popen(
            [sys.executable, *arguments],
            cwd=ROOT / "backend",
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        processes[role] = process
        report["roles"][role] = {
            "pid": process.pid,
            "snowflake_worker_id": ROLE_IDS[role],
            "arguments": arguments,
        }
        checkpoint()

    def consumers():
        assert all(process.poll() is None for process in processes.values()), {
            role: process.poll() for role, process in processes.items()
        }
        with celery.connection_for_read() as connection:
            channel = connection.channel()
            result = {}
            for role, queue in queues.items():
                try:
                    declared = channel.queue_declare(queue.name, passive=True)
                except BrokerObjectMissing:
                    return {}
                result[role] = {
                    "queue": queue.name,
                    "consumers": declared.consumer_count,
                    "messages": declared.message_count,
                }
            return result

    def health():
        assert processes["api"].poll() is None
        try:
            with urlopen(url + "/openapi.json", timeout=2) as response:
                schema = json.loads(response.read())
                return (
                    schema
                    if "/api/v1/assistant/global/conversations/{conversation_id}/state"
                    in schema["paths"]
                    else None
                )
        except (URLError, TimeoutError):
            return None

    try:
        start(
            "api",
            [
                "-m",
                "uvicorn",
                "short_drama.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-access-log",
                "--log-level",
                "warning",
            ],
        )
        eventually(health, lambda value: value is not None, timeout=30)
        report["api_lifespan"] = "normal readiness passed and OpenAPI served"
        for role, queue in queues.items():
            concurrency = "4" if role == "text" else "1" if role == "render" else "2"
            arguments = [
                "worker",
                "--pool=threads",
                f"--concurrency={concurrency}",
                "-Q",
                queue.name,
                f"--hostname={role}-{uuid4().hex[:8]}@%h",
                "--loglevel=WARNING",
            ]
            if generation_observer_module and role in {"text", "image", "video"}:
                arguments.append("--include=" + generation_observer_module)
            start(role, ["-m", "celery", "-A", "short_drama.tasks.celery_app:app", *arguments])
        report["consumers"] = eventually(
            consumers,
            lambda value: (
                len(value) == 6
                and all(row["consumers"] == 1 and row["messages"] == 0 for row in value.values())
            ),
            timeout=40,
        )
        start("scheduler", ["-m", "short_drama.tasks.runtime.runtime"])
        checkpoint()
        yield url, report, processes
        report["final_consumers"] = consumers()
        assert len(report["final_consumers"]) == 6
        assert all(row["messages"] == 0 for row in report["final_consumers"].values())
    finally:
        # 仅结束本 context 返回的自建进程；先停 Publisher，避免清理时再入列。
        for role in ("scheduler", "agent", "render", "audio", "video", "image", "text", "api"):
            process = processes.get(role)
            if process is not None:
                if process.poll() is None:
                    stop_browser(process)
                    process.wait(10)
                report["cleanup"][role] = process.poll() is not None
        for output in logs:
            output.close()
        try:
            if report.get("consumers"):
                report["consumers_detach_ms"] = wait_for_consumers_to_detach(
                    celery, list(queues.values())
                )
            with celery.connection_for_write() as connection:
                for queue in [*queues.values(), *dead_queues]:
                    assert queue.name.startswith(settings.generation_queue_namespace + ".")
                    try:
                        queue(connection).delete(if_unused=True, if_empty=False)
                    except BrokerObjectMissing:
                        pass
                exchange(connection).delete(if_unused=True)
                dead_exchange(connection).delete(if_unused=True)
            report["cleanup"]["queues_and_exchanges"] = True
        finally:
            celery.close()
            checkpoint()
