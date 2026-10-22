"""显式授权：正式 API/Scheduler/Celery/AMQP 与真实 Agnes 的原助手界面验收。"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from amqp.exceptions import NotFound as BrokerObjectMissing
from kombu import Queue
from sqlalchemy import func, select

from short_drama.agent.coordinator.publisher import AgentRabbitSender
from short_drama.domain import AsyncTask
from short_drama.domain.agent import (
    AgentArtifact,
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentTurn,
)
from short_drama.tasks.celery_app import agent_queue, make_celery, render_queue, topology
from tests.integration.canvas.assistant.test_canvas_agnes_live import assert_no_secret, live_key
from tests.integration.canvas.document.test_canvas_browser import stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import (
    eventually,
    wait_for_consumers_to_detach,
    worker_environment,
)
from tests.integration.canvas.resources.test_canvas_resources import canvas
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.assistant.test_project_assistant_agnes_live import PROMPTS, SOURCE
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)
from tests.integration.standard.identity.test_identity_collaboration import (
    project as standard_project,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_AGNES_LIVE",
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
                "RUN_CANVAS_BROKER_INTEGRATION",
            )
        ),
        reason="Explicit live Agnes, real AMQP, isolated storage and browser opt-in are required",
    ),
]
ROOT = Path(__file__).resolve().parents[5]
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
                return schema if "/api/v1/assistant/runs/{run_id}" in schema["paths"] else None
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
            start(
                role,
                [
                    "-m",
                    "tests.support.integration.assistant_broker_worker",
                    str(directory),
                    *arguments,
                ]
                if role == "agent"
                else ["-m", "celery", "-A", "short_drama.tasks.celery_app:app", *arguments],
            )
        report["consumers"] = eventually(
            consumers,
            lambda value: (
                len(value) == 6
                and all(row["consumers"] == 1 and row["messages"] == 0 for row in value.values())
            ),
            timeout=40,
        )
        start("scheduler", ["-m", "short_drama.tasks.runtime"])
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


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("protocol", ["chat-completion", "openai-response"])
@pytest.mark.parametrize("surface", ["canvas", "standard"])
def test_original_assistant_real_agnes_through_formal_eight_roles(
    resource_app, tmp_path, surface, protocol, width
):
    app, factory, current_settings = resource_app
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    app.state.settings = current_settings.model_copy(update={"agent_enabled": True})
    suffix = surface[:1] + ("chat" if protocol == "chat-completion" else "responses") + str(width)
    owner, user = account(resource_app, "ab_o_" + suffix)
    foreign, _ = account(resource_app, "ab_m_" + suffix)
    outsider, _ = account(resource_app, "ab_x_" + suffix)
    if surface == "canvas":
        project, path, document = canvas(owner, "assistant-broker-" + uuid4().hex)
        document["nodes"] = [
            {
                "id": "assistant-source",
                "type": "text",
                "title": "礼盒记录",
                "position": {"x": 0, "y": 0},
                "width": 360,
                "height": 180,
                "metadata": {"content": SOURCE, "status": "success"},
            }
        ]
        saved = owner.post(
            path + "/commits",
            headers={"Idempotency-Key": "assistant-source"},
            json={"expected_row_version": document["revision"], "source_document": document},
        )
        assert saved.status_code == 200
        original = owner.get(path + "/my-document").json()["source_document"]
        source_key, context_kind = original["id"], "canvas"
    else:
        project = standard_project(owner)
        source_key, context_kind = project["id"], "project"
        path = f"/api/v1/projects/{project['id']}"
        writing = owner.get(path + "/writing").json()
        saved = owner.put(
            path + "/novel", json={"content_version": writing["content_version"], "content": SOURCE}
        )
        assert saved.status_code == 200
        original = owner.get(path + "/writing").json()
    secret = live_key()
    created = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "Agnes正式助手-" + protocol,
            "service_type": "text",
            "provider": "Agnes",
            "model_key": "agnes-3.0-flash",
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": secret,
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": protocol},
        },
    )
    if created.status_code != 201:
        pytest.fail("Saved real assistant model failed: HTTP " + str(created.status_code))
    model = created.json()
    assert_no_secret(model, secret)
    browser_port = int(os.environ.get("AGNES_ASSISTANT_BROKER_BROWSER_PORT", "4197"))
    namespace = "assistant_broker_live_" + uuid4().hex
    settings = app.state.settings.model_copy(
        update={
            "public_origin": f"http://127.0.0.1:{browser_port}",
            "generation_queue_namespace": namespace,
            "smtp_host": "",
            "smtp_from": "",
            "render_scratch_root": str(tmp_path / "render-scratch"),
        }
    )
    assert re.fullmatch(r"assistant_broker_live_[a-f0-9]{32}", namespace)
    config = {
        "username": user["username"],
        "password": PASSWORD,
        "projectId": project["id"],
        "sourceKey": source_key,
        "surface": surface,
        "contextKind": context_kind,
        "inputMode": "source",
        "expectedColor": "红色",
        "modelId": model["id"],
        "protocol": protocol,
        "width": width,
        "browserPort": browser_port,
        "prompts": PROMPTS,
        "releasePath": str(tmp_path / "allow-provider"),
        "outputDirectory": str(tmp_path / "browser"),
    }
    with official_stack(factory, settings, tmp_path) as (url, stack, workers):
        config["apiUrl"] = url
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-project-assistant-agnes-python.mjs"],
            cwd=ROOT / "frontend" / "canvas",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=420)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            process.communicate(timeout=10)
            pytest.exit("Formal live assistant timed out; reconcile before any further POST", 1)
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert_no_secret(output + errors, secret)
        if process.returncode:
            pytest.exit(
                "Formal live assistant requires reconciliation: " + errors + "\n" + output, 1
            )
        browser = json.loads(output)
        assert browser["step"] == "complete"
        assert not browser["page_errors"] and not browser["http_errors"]
        run_ids = [int(identifier) for identifier in browser["run_ids"]]
        deliveries = eventually(
            lambda: read_json(tmp_path / "worker-report.json"),
            lambda value: (
                value is not None
                and len(value["deliveries"]) == 2
                and all(row["completed"] for row in value["deliveries"])
            ),
        )
        assert workers["agent"].pid in {deliveries["pid"], deliveries["parent_pid"]}
        stack["roles"]["agent"]["execution_pid"] = deliveries["pid"]
        original_deliveries = deliveries["deliveries"]
        assert {int(row["run_id"]) for row in original_deliveries} == set(run_ids)
        with factory() as session:
            versions = {
                row.id: row.message_version
                for row in session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))
            }
        for row in original_deliveries:
            assert int(row["message_version"]) + 1 == versions[int(row["run_id"])]
            assert row["state"] == "SUCCESS"
            assert row["delivery"]["exchange"] == namespace + ".tasks"
            assert row["delivery"]["routing_key"] == "agent"
            assert row["delivery"]["redelivered"] is False
        # 两条额外消息只核对原任务版本围栏，不能再次调用供应商。
        sender = AgentRabbitSender(settings)
        try:
            original_version = int(
                next(row for row in original_deliveries if int(row["run_id"]) == run_ids[0])[
                    "message_version"
                ]
            )
            sender(run_ids[0], original_version)
            sender(run_ids[0], original_version - 1)
        finally:
            sender.app.close()
        deliveries = eventually(
            lambda: read_json(tmp_path / "worker-report.json"),
            lambda value: (
                value is not None
                and len(value["deliveries"]) == 4
                and all(row["completed"] for row in value["deliveries"])
            ),
        )
        assert all(row["state"] == "SUCCESS" for row in deliveries["deliveries"])
        assert all(worker.poll() is None for worker in workers.values())
    provider = read_json(tmp_path / "provider-report.json")
    assert provider is not None
    assert provider["worker_pid"] == stack["roles"]["agent"]["execution_pid"]
    assert provider["snowflake_worker_id"] == ROLE_IDS["agent"]
    assert browser["message_posts"] == provider["submissions"] == 2
    assert len(provider["requests"]) == len(provider["results"]) == 2
    endpoint = "/v1/chat/completions" if protocol == "chat-completion" else "/v1/responses"
    assert all(
        row["method"] == "POST"
        and row["host"] == "api.agnes-ai.cn"
        and row["path"] == endpoint
        and row["http_status"] == 200
        and row["options"]["stream"] is True
        for row in provider["requests"]
    )
    assert all(stack["cleanup"].values())
    assert len(stack["roles"]) == 8
    assert len({row["pid"] for row in stack["roles"].values()}) == 8
    conversation_id = int(browser["conversation_id"])
    with factory() as session:
        conversation = session.get(AgentConversation, conversation_id)
        assert conversation.scope_version == 2 and conversation.owner_user_id == int(user["id"])
        runs = list(session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids))))
        assert len(runs) == 2 and all(row.status == "succeeded" for row in runs)
        for row in runs:
            assert row.config_snapshot["model_key"] == "agnes-3.0-flash"
            assert row.checkpoint["purpose"] == "assistant_chat"
            assert row.checkpoint["authorization"]["mode"] == "discuss"
            assert row.checkpoint["authorization"]["steps"] == []
            assert_no_secret(row.checkpoint, secret)
        turns = list(session.scalars(select(AgentTurn).where(AgentTurn.run_id.in_(run_ids))))
        assert len(turns) == 2 and all(row.request_messages and row.response for row in turns)
        first, second = sorted(turns, key=lambda row: run_ids.index(row.run_id))
        first_input = first.request_messages[0]["kwargs"]
        second_input = second.request_messages[0]["kwargs"]
        assert SOURCE in first_input["instructions"]
        assert first_input["user_prompt"] == PROMPTS[0]
        first_run = next(row for row in runs if row.id == run_ids[0])
        source = first_run.checkpoint["context_snapshot"]
        assert source["kind"] == context_kind and source["id"] == source_key
        assert SOURCE in json.dumps(source, ensure_ascii=False)
        assert (
            next(row for row in runs if row.id == run_ids[1]).checkpoint.get("context_snapshot")
            is None
        )
        assert second_input["user_prompt"] == PROMPTS[1]
        history = json.loads(second_input["instructions"].split("本次资料：", 1)[1])
        assert history["source"] is None and history["skills"] == []
        assert [row["role"] for row in history["history"]] == ["user", "assistant"]
        assert history["history"][0]["content"] == PROMPTS[0]
        assert (
            "红色" in history["history"][1]["content"]
            and "礼盒" in history["history"][1]["content"]
        )
        assert all(len(row.request_messages) == 2 for row in turns)
        assert_no_secret([row.request_messages for row in turns], secret)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentEvent)
                .where(AgentEvent.run_id.in_(run_ids), AgentEvent.event_type == "assistant.delta")
            )
            > 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentToolCall)
                .where(AgentToolCall.run_id.in_(run_ids))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentArtifact)
                .where(AgentArtifact.project_id == int(project["id"]))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AsyncTask)
                .where(AsyncTask.project_id == int(project["id"]))
            )
            == 0
        )
        messages = list(
            session.scalars(
                select(AgentMessage)
                .where(AgentMessage.conversation_id == conversation_id)
                .order_by(AgentMessage.seq)
            )
        )
        assert [row.role for row in messages] == ["user", "assistant", "user", "assistant"]
        assert "红色" in messages[1].content and "礼盒" in messages[1].content
        assert "礼盒" in messages[3].content
        assert_no_secret([row.content for row in messages], secret)
    conversation_path = "/api/v1/assistant/conversations/" + str(conversation_id)
    assert owner.get(conversation_path).status_code == 200
    for other in (foreign, outsider):
        for suffix in ("", "/messages", "/state", "/events", "/attachments"):
            assert other.get(conversation_path + suffix).status_code == 404
        for identifier in run_ids:
            assert other.get("/api/v1/assistant/runs/" + str(identifier)).status_code == 404
        assert other.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
    assert (
        foreign.get(
            "/api/v1/assistant/conversations", params={"project_id": project["id"]}
        ).status_code
        == 404
    )
    if surface == "canvas":
        current = owner.get(path + "/my-document").json()["source_document"]
        for field in ("revision", "nodes", "connections"):
            assert current[field] == original[field]
        assert foreign.get(path + "/my-document").status_code == 404
        visible = current
        assert visible["nodes"][0]["metadata"]["content"] == SOURCE
    else:
        current = owner.get(path + "/writing").json()
        assert foreign.get(path + "/writing").status_code == 404
        visible = current
        assert current == visible == original and visible["novel"]["content"] == SOURCE
    assert_no_secret(visible, secret)
    assert_no_secret(provider, secret)
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "surface": surface,
                "protocol": protocol,
                "width": width,
                "browser": browser,
                "provider": provider,
                "stack": stack,
                "broker_deliveries": deliveries,
                "python_postchecks": "passed",
                "media_worker_business_paths": "not exercised",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
