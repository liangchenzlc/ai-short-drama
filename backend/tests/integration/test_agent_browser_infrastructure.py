"""浏览器、真实 API 与隔离 RabbitMQ 链路；模型为本地 HTTP 替身，不调用付费供应商。"""

import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import test_agent_conversations as conversation_tests
import test_agent_unified_creation as unified_tests
import uvicorn
from celery import current_app
from celery.contrib.testing.worker import start_worker
from celery.signals import task_postrun
from sqlalchemy import select
from test_agent_unified_creation import scoped_setup, stream_reply

from short_drama.agent.model_gateway import AgentModelGateway
from short_drama.agent.publisher import AgentPublisher, AgentRabbitSender
from short_drama.agent.runtime import AgentRuntime
from short_drama.core.config import Settings
from short_drama.core.identity import token_hash
from short_drama.db.readiness import assert_agent_ready, assert_identity_ready
from short_drama.domain import AIModelConfig, Asset
from short_drama.domain.agent import AgentArtifact, AgentRun
from short_drama.domain.collaboration import UserSession
from short_drama.main import create_app
from short_drama.service.base import utcnow
from short_drama.storage.minio import MinioStorage
from short_drama.tasks import agent_worker
from short_drama.utils.snowflake import next_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_AGENT_INFRA_INTEGRATION") != "1",
        reason="Set RUN_AGENT_INFRA_INTEGRATION=1 for real browser/API/local broker integration",
    ),
]
workspace = conversation_tests.workspace
tool_provider = unified_tests.tool_provider


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def api_server(factory, settings):
    """关闭默认 lifespan，防止它覆盖隔离 factory 并连接业务数据库。"""
    app = create_app(settings)
    storage = MinioStorage(settings)
    app.state.session_factory = factory
    app.state.agent_schema_ready = True
    app.state.storage = storage
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                lifespan="off",
                access_log=False,
                log_config=None,
                log_level="error",
            )
        )
        errors = []

        def serve():
            try:
                server.run(sockets=[listener])
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=serve, name="agent-test-api", daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                threading.Event().wait(0.05)
            assert server.started, f"Isolated API did not start: {errors!r}"
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(5)
            if thread.is_alive():
                server.force_exit = True
                thread.join(5)
            storage.close()
            assert not thread.is_alive(), "Isolated API thread did not stop"
            assert not errors, errors


def cleanup_broker(sender, namespace):
    assert namespace.startswith("agent_browser_acceptance_")
    with sender.app.connection_for_write() as broker:
        broker.ensure_connection(max_retries=0)
        resources = [("queue", suffix) for suffix in (".tasks.agent", ".tasks.agent.dead")]
        resources += [("exchange", suffix) for suffix in (".tasks", ".dead")]
        for kind, suffix in resources:
            channel = broker.channel()
            try:
                if kind == "queue":
                    channel.queue_delete(queue=namespace + suffix)
                else:
                    channel.exchange_delete(exchange=namespace + suffix)
            except Exception as error:
                if getattr(error, "reply_code", None) != 404:
                    raise
            finally:
                try:
                    channel.close()
                except Exception:
                    pass


def stop_browser_process(process):
    """仅停止本测试创建的进程树，超时时也回收 Chromium 子进程。"""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_browser_sends_to_real_api_and_agent_worker_displays_private_candidate(
    workspace, tool_provider, monkeypatch
):
    node = shutil.which("node")
    assert node, "Node.js is required for the opt-in real browser test"
    root = Path(__file__).resolve().parents[3]
    frontend = root / "frontend"
    assert (frontend / "node_modules" / "@playwright" / "test").is_dir(), (
        "Run npm ci in frontend before the opt-in real browser test"
    )
    base_url, provider = tool_provider
    factory, conversation_id, model_id, target_id, scope, settings = scoped_setup(
        workspace, base_url
    )
    frontend_port = free_port()
    frontend_origin = f"http://127.0.0.1:{frontend_port}"
    namespace = "agent_browser_acceptance_" + uuid4().hex
    # 仅复制已配置的 broker；数据库 factory 与模型地址始终属于本测试。
    broker_settings = Settings()
    for field in (
        "rabbitmq_host",
        "rabbitmq_port",
        "rabbitmq_user",
        "rabbitmq_password",
        "rabbitmq_vhost",
        "rabbitmq_tls",
    ):
        setattr(settings, field, getattr(broker_settings, field))
    settings.public_origin = frontend_origin
    settings.generation_queue_namespace = namespace
    token, csrf = uuid4().hex, uuid4().hex
    with factory.begin() as session:
        settings.db_name = session.get_bind().url.database
        assert settings.db_name.endswith("_test")
        assert_identity_ready(session.get_bind(), settings)
        assert_agent_ready(session.get_bind(), settings)
        asset = session.get(Asset, target_id)
        asset.state = "unconfirmed"
        asset.media_id = None
        original_description = asset.description
        session.add(
            UserSession(
                id=next_id(),
                user_id=1,
                token_hash=token_hash(token),
                csrf_hash=token_hash(csrf),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    provider["responses"] = [
        (
            200,
            stream_reply(
                call=(
                    "prepare",
                    "prepare_task",
                    {"kind": "asset_patch", "instructions": "按要求修改"},
                )
            ),
            "text/event-stream",
        ),
        (
            200,
            stream_reply(
                call=(
                    "candidate",
                    "create_candidate",
                    {"step_id": "single", "patch": {"description": "更冷峻的人物"}},
                )
            ),
            "text/event-stream",
        ),
        (200, stream_reply(), "text/event-stream"),
    ]
    monkeypatch.setattr(
        agent_worker,
        "runtime",
        lambda: AgentRuntime(factory, settings, AgentModelGateway(settings)),
    )
    previous_app = current_app._get_current_object()
    sender = AgentRabbitSender(settings)
    publisher = AgentPublisher(factory, settings, send=sender)
    stop = threading.Event()
    publish_errors, deliveries = [], []

    def publish():
        try:
            while not stop.wait(0.1):
                publisher.tick()
        except Exception as error:
            publish_errors.append(error)
            stop.set()

    def completed(task=None, args=None, state=None, **_kwargs):
        if task is not None and task.name == "short_drama.execute_agent":
            deliveries.append((args, state))

    task_postrun.connect(completed, weak=False)
    try:
        sender.app.loader.import_default_modules()
        with start_worker(
            sender.app,
            pool="threads",
            concurrency=1,
            queues=[sender.queue.name],
            perform_ping_check=False,
            shutdown_timeout=15,
            loglevel="ERROR",
        ):
            thread = threading.Thread(target=publish, name="agent-test-publisher", daemon=True)
            thread.start()
            try:
                with api_server(factory, settings) as api_origin:
                    config = {
                        "frontendPort": frontend_port,
                        "frontendOrigin": frontend_origin,
                        "apiOrigin": api_origin,
                        "token": token,
                        "csrf": csrf,
                        "projectId": str(workspace[1]),
                        "episodeId": str(workspace[2]),
                        "conversationId": str(conversation_id),
                        "targetId": str(target_id),
                        "modelId": str(model_id),
                        "scope": scope,
                        "executablePath": os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"),
                    }
                    process = subprocess.Popen(
                        [node, "e2e/support/agent-browser-infrastructure.mjs"],
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
                        output, errors = process.communicate(json.dumps(config), timeout=120)
                    except subprocess.TimeoutExpired:
                        stop_browser_process(process)
                        output, errors = process.communicate(timeout=10)
                        pytest.fail(f"Real browser timed out: {errors}\n{output}")
                    finally:
                        if process.poll() is None:
                            stop_browser_process(process)
                            process.communicate(timeout=10)
                    assert process.returncode == 0, f"Real browser failed: {errors}\n{output}"
                    browser_result = json.loads(output)
                    assert browser_result["candidateVisible"] is True
                    with factory() as session:
                        run = session.get(AgentRun, int(browser_result["runId"]))
                        artifact = session.scalars(select(AgentArtifact)).one()
                        assert run.status == "succeeded"
                        assert run.checkpoint["authorization"]["mode"] == "single"
                        assert artifact.status == "ready" and artifact.created_by == 1
                        assert artifact.target_asset_id == target_id
                        assert artifact.proposed_patch == {"description": "更冷峻的人物"}
                        assert session.get(Asset, target_id).description == original_description
                        assert session.get(AIModelConfig, model_id).capability_cache is None
                    assert len(provider["requests"]) == 3 and provider["responses"] == []
                    assert deliveries and all(state == "SUCCESS" for _, state in deliveries)
                    assert not publish_errors, publish_errors
            finally:
                stop.set()
                thread.join(10)
                assert not thread.is_alive(), "Isolated publisher thread did not stop"
    finally:
        task_postrun.disconnect(completed)
        try:
            cleanup_broker(sender, namespace)
        finally:
            sender.app.close()
            previous_app.set_current()
            previous_app.set_default()
