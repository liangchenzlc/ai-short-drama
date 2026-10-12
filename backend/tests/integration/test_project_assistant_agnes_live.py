"""显式授权：共用助手原界面与真实 Agnes，使用线程执行器和隔离数据库/存储。"""

import asyncio
import base64
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from sqlalchemy import create_engine, func, select

from short_drama.agent.model_gateway import AgentGatewayError, AgentModelGateway
from short_drama.agent.recovery import recover
from short_drama.agent.runtime import AgentRuntime
from short_drama.db.readiness import (
    assert_agent_ready,
    assert_identity_ready,
    inspect_agent_schema,
)
from short_drama.db.session import configure_mysql, session_factory
from short_drama.domain import AsyncTask, MediaFile
from short_drama.domain.agent import (
    AgentArtifact,
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentTurn,
)
from short_drama.domain.agent_context import AgentAttachment, AgentSkill
from short_drama.main import create_app
from short_drama.service.base import utcnow
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_agnes_live import (
    ObservedTransport,
    assert_no_secret,
    live_key,
)
from tests.integration.test_canvas_browser import stop_browser
from tests.integration.test_canvas_resources import canvas
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.integration.test_identity_collaboration import project as standard_project

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_AGNES_LIVE",
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
            )
        ),
        reason="Explicit live Agnes, isolated storage and browser opt-in are required",
    ),
]
PROMPTS = [
    "当前画布描述的道具是什么？只回复颜色和道具名，不要执行任何操作。",
    "延续上一条，我们刚才讨论的道具是什么？只回复道具名，不要执行任何操作。",
]
SOURCE = "唯一道具是纯红色的空白方形礼盒。"
IMAGE_PROMPT = "本次图片里的道具是什么？请以图片本身为准，只回复颜色和道具名，不要执行操作。"
SKILL_SOURCE = "# 图片解读\n只根据本次附件的可见内容判断，不用文件名推测，回复简洁，不执行操作。"
SKILL_UPDATED = "# 图片解读新版\n仅提供简短的创作建议，不执行操作。"
LIFECYCLE_MODES = ("stop-after-response", "recover-after-response", "interrupt-after-headers")
UNKNOWN_MESSAGE = "请求可能已经提交，但结果暂时无法确认。请先核对当前任务，避免重复生成和计费。"


def inline_image_hashes(value):
    if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
        return [hashlib.sha256(base64.b64decode(value.split(",", 1)[1])).hexdigest()]
    if isinstance(value, dict):
        return [digest for item in value.values() for digest in inline_image_hashes(item)]
    if isinstance(value, list):
        return [digest for item in value for digest in inline_image_hashes(item)]
    return []


class AssistantObservedTransport(ObservedTransport):
    """记录供应商公开拒绝原因；不保存错误响应回显的输入、内联媒体或鉴权头。"""

    def request(self, method, url, **kwargs):
        status, headers, data = super().request(method, url, **kwargs)
        if status < 400:
            return status, headers, data
        try:
            response = json.loads(data)
        except (ValueError, UnicodeError):
            response = {}
        if not isinstance(response, dict):
            response = {}
        secrets = [
            value.removeprefix("Bearer ")
            for name, value in kwargs.get("headers", {}).items()
            if name.lower() in {"authorization", "x-api-key", "api-key"}
        ]

        def safe_text(value):
            text = str(value)
            for secret in secrets:
                if secret:
                    text = text.replace(secret, "[redacted]")
            text = re.sub(r"data:[^\s\"']+", "[inline media]", text)
            text = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", text)
            return text[:1000]

        details = response.get("error", response.get("detail", {}))
        if isinstance(details, str):
            public = {"message": safe_text(details)}
        elif isinstance(details, dict):
            public = {
                name: safe_text(details[name])
                for name in ("code", "type", "message", "param")
                if name in details
            }
        elif isinstance(details, list):
            public = {
                "validation": [
                    {name: safe_text(item[name]) for name in ("loc", "msg", "type") if name in item}
                    for item in details[:10]
                    if isinstance(item, dict)
                ]
            }
        else:
            public = {}
        self.report["requests"][-1]["public_error"] = public
        self.checkpoint()
        return status, headers, data


@contextmanager
def assistant_api(factory, settings, storage):
    """沿启动检查读取随机库；不让 lifespan 连接业务库。"""
    engine = factory.kw["bind"]
    assert_identity_ready(engine, settings)
    assert_agent_ready(engine, settings)
    with engine.connect() as connection:
        assert inspect_agent_schema(connection) == {"status": "ready", "gaps": []}
    app = create_app(settings)
    app.state.session_factory = factory
    app.state.storage = storage
    app.state.agent_schema_ready = True
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
                errors.append(type(error).__name__)

        thread = threading.Thread(target=serve, name="assistant-live-api", daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                threading.Event().wait(0.05)
            assert server.started and not errors
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(5)
            if thread.is_alive():
                server.force_exit = True
                thread.join(5)
            assert not thread.is_alive() and not errors


class LiveAssistantGateway(AgentModelGateway):
    """只在第一次真实 POST 前暂停，原模型请求、响应和 SSE 全部委托真实实现。"""

    def __init__(self, settings, directory, stopped):
        super().__init__(settings)
        self.directory, self.stopped = directory, stopped
        self.report = {
            "scope": "real Agnes through AgentModelGateway and AgentRuntime",
            "submissions": 0,
            "requests": [],
            "downloads": [],
            "results": [],
        }
        self.transport = AssistantObservedTransport(
            self.transport, self.report, directory, self.checkpoint
        )

    def checkpoint(self):
        (self.directory / "provider-report.json").write_text(
            json.dumps(self.report, indent=2), encoding="utf-8"
        )

    async def run_segment(self, snapshot, credential, **kwargs):
        if not self.report["submissions"]:
            deadline = time.monotonic() + 90
            while not (self.directory / "allow-provider").exists():
                if self.stopped.is_set() or time.monotonic() >= deadline:
                    raise AgentGatewayError("live_test_not_released")
                await asyncio.sleep(0.05)
        if self.stopped.is_set():
            raise AgentGatewayError("live_test_not_released")
        self.report["submissions"] += 1
        self.checkpoint()
        try:
            result = await super().run_segment(snapshot, credential, **kwargs)
        except AgentGatewayError as error:
            self.report["error"] = {
                "code": error.code,
                "accepted_unknown": error.accepted_unknown,
                "http_status": error.http_status,
            }
            self.checkpoint()
            raise
        assert isinstance(result.output, str), "Pure chat returned a tool intent"
        self.report["results"].append(
            {"completed": True, "text": result.output, "protocol": result.protocol}
        )
        self.checkpoint()
        return result


class LifecycleObservedTransport(AssistantObservedTransport):
    """在真实200响应头之后暂停读取，供测试终止自己创建的执行器进程。"""

    def request(self, method, url, **kwargs):
        callback = kwargs.get("on_headers")

        def received(status, headers):
            self.report["provider_headers"] = {
                "status": status,
                "content_type": next(
                    (value for name, value in headers.items() if name.lower() == "content-type"),
                    "",
                ),
            }
            self.checkpoint()
            if callback is not None:
                callback(status, headers)
            if status == 200 and self.report["lifecycle_mode"] == "interrupt-after-headers":
                (self.directory / "supplier-ready").touch()
                while not (self.directory / "shutdown-worker").exists():
                    threading.Event().wait(0.05)
                raise AgentGatewayError("live_executor_shutdown", accepted_unknown=True)

        kwargs["on_headers"] = received
        return super().request(method, url, **kwargs)


class LiveLifecycleGateway(LiveAssistantGateway):
    """真实完整回执持久化后暂停；恢复时委托原离线重放，绝不另发POST。"""

    def __init__(self, settings, directory, mode):
        super().__init__(settings, directory, threading.Event())
        saved = directory / "provider-report.json"
        if saved.exists():
            self.report = json.loads(saved.read_text(encoding="utf-8"))
        self.report.setdefault("lifecycle_mode", mode)
        self.report.setdefault("replays", 0)
        self.transport = LifecycleObservedTransport(
            self.transport.delegate, self.report, directory, self.checkpoint
        )
        self.checkpoint()

    async def run_segment(self, snapshot, credential, **kwargs):
        callback = kwargs["on_response"]

        async def durable_response(raw):
            await callback(raw)
            self.report["complete_raw_persisted"] = True
            self.checkpoint()
            (self.directory / "supplier-ready").touch()
            while not (self.directory / "allow-finalize").exists():
                if (self.directory / "shutdown-worker").exists():
                    raise AgentGatewayError("live_executor_shutdown", accepted_unknown=True)
                await asyncio.sleep(0.05)

        kwargs["on_response"] = durable_response
        return await super().run_segment(snapshot, credential, **kwargs)

    async def replay_segment(self, snapshot, credential, **kwargs):
        self.report["replays"] += 1
        self.checkpoint()
        result = await super().replay_segment(snapshot, credential, **kwargs)
        self.report["results"].append(
            {"completed": True, "text": result.output, "protocol": result.protocol, "replay": True}
        )
        self.checkpoint()
        return result


def lifecycle_executor(database_url, settings, directory, owner_id, mode):
    """任务独占子进程；Settings和加密快照只通过进程内存传递。"""
    assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database_url.database or "")
    os.environ["SNOWFLAKE_WORKER_ID"] = str(settings.snowflake_worker_id)
    engine = configure_mysql(create_engine(database_url, pool_pre_ping=True, hide_parameters=True))
    factory = session_factory(engine)
    gateway = LiveLifecycleGateway(settings, directory, mode)
    runtime = AgentRuntime(factory, settings, gateway)
    try:
        while not (directory / "shutdown-worker").exists():
            with factory() as session:
                pending = session.execute(
                    select(AgentRun.id, AgentRun.message_version).where(
                        AgentRun.initiated_by == owner_id,
                        AgentRun.status.in_({"queued", "running"}),
                        AgentRun.message_status == "pending",
                        AgentRun.next_run_at <= utcnow(),
                    )
                ).all()
            for identifier, version in pending:
                runtime.execute_one(identifier, version)
                (directory / "executor-finished").touch()
            threading.Event().wait(0.05)
    except BaseException as error:
        (directory / "executor-error.json").write_text(
            json.dumps({"type": type(error).__name__}), encoding="utf-8"
        )
        raise SystemExit(1) from None
    finally:
        engine.dispose()


def supervise_lifecycle(factory, settings, directory, owner_id, mode, stopped):
    """中止原进程后等待配置的真实租约到期；不改写租约、回执或业务状态。"""
    context = multiprocessing.get_context("spawn")
    children = []
    audit = {
        "lease_seconds": settings.agent_lease_seconds,
        "executor_worker_id": settings.snowflake_worker_id,
        "children": [],
        "recoveries": 0,
    }

    def checkpoint():
        (directory / "supervisor-report.json").write_text(
            json.dumps(audit, indent=2), encoding="utf-8"
        )

    def start():
        child = context.Process(
            target=lifecycle_executor,
            args=(factory.kw["bind"].url, settings, directory, owner_id, mode),
            name="assistant-agnes-owned-executor",
        )
        child.start()
        children.append(child)
        audit["children"].append({"pid": child.pid})
        checkpoint()
        return child

    child = start()
    interrupted = recovered = False
    try:
        while not stopped.wait(0.1):
            if (directory / "executor-error.json").exists():
                raise AssertionError(
                    "Task-owned assistant executor failed; inspect safe error report"
                )
            if (directory / "interrupt-worker").exists() and not interrupted:
                with factory() as session:
                    run = session.scalar(select(AgentRun).where(AgentRun.initiated_by == owner_id))
                    turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == run.id))
                    assert run.status == "running" and turn.status == "sent"
                    raw = (turn.response or {}).get("raw")
                    assert bool(raw) is (mode == "recover-after-response")
                    audit.update(
                        {
                            "run_id": str(run.id),
                            "message_version": run.message_version,
                            "lease_until_before_kill": run.lease_until.isoformat(),
                            "request_sha256": hashlib.sha256(
                                json.dumps(turn.request_messages, sort_keys=True).encode()
                            ).hexdigest(),
                            "raw_sha256": hashlib.sha256(
                                json.dumps(raw, sort_keys=True).encode()
                            ).hexdigest(),
                        }
                    )
                assert child.is_alive()
                child.terminate()
                child.join(10)
                assert not child.is_alive() and child.exitcode is not None
                audit["children"][0].update({"terminated": True, "exit_code": child.exitcode})
                audit["killed_at"] = utcnow().isoformat()
                checkpoint()
                interrupted = True
            if interrupted and not recovered:
                count = recover(factory, settings)
                if count:
                    assert count == 1
                    audit["recoveries"] = count
                    audit["recovered_at"] = utcnow().isoformat()
                    assert audit["recovered_at"] >= audit["lease_until_before_kill"]
                    if mode == "recover-after-response":
                        child = start()
                    else:
                        runtime = AgentRuntime(factory, settings, AgentModelGateway(settings))
                        runtime.execute_one(int(audit["run_id"]), audit["message_version"])
                        with factory() as session:
                            current = session.get(AgentRun, int(audit["run_id"]))
                            assert current.status == "failed"
                            assert current.error == {"code": "agent_acceptance_unknown"}
                            runtime.execute_one(current.id, current.message_version)
                        (directory / "executor-finished").touch()
                    checkpoint()
                    recovered = True
            if not interrupted and not child.is_alive():
                raise AssertionError("Task-owned assistant executor exited unexpectedly")
    finally:
        (directory / "shutdown-worker").touch()
        (directory / "allow-finalize").touch()
        (directory / "allow-provider").touch()
        for child in children:
            child.join(10)
            if child.is_alive():
                child.terminate()
                child.join(10)
            assert not child.is_alive()
        audit["all_children_stopped"] = True
        checkpoint()


def assert_lifecycle_postchecks(
    factory,
    settings,
    directory,
    browser,
    provider,
    mode,
    owner,
    member,
    outsider,
    user,
    project,
    model,
    path,
    original,
    surface,
    protocol,
    secret,
):
    assert browser["message_posts"] == provider["submissions"] == 1
    assert browser["cookies_only_running_recovery_after_provider_response"] is True
    assert browser["fresh_terminal_recovery_same_conversation"] is True
    assert provider["provider_headers"]["status"] == 200
    assert provider["provider_headers"]["content_type"].startswith("text/event-stream")
    assert len(provider["requests"]) == 1
    request = provider["requests"][0]
    assert request["method"] == "POST" and request["host"] == "api.agnes-ai.cn"
    assert request["path"] == (
        "/v1/chat/completions" if protocol == "chat-completion" else "/v1/responses"
    )
    assert request["options"]["stream"] is True
    supervisor = json.loads((directory / "supervisor-report.json").read_text("utf-8"))
    assert supervisor["all_children_stopped"] is True
    assert supervisor["lease_seconds"] == settings.agent_lease_seconds == 150
    expected_status = {
        "stop-after-response": "cancelled",
        "recover-after-response": "succeeded",
        "interrupt-after-headers": "failed",
    }[mode]
    identifier = int(browser["run_ids"][0])
    conversation_id = int(browser["conversation_id"])
    with factory() as session:
        run = session.get(AgentRun, identifier)
        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == identifier))
        conversation = session.get(AgentConversation, conversation_id)
        assert run.status == expected_status and run.usage["decision_calls"] == 1
        assert conversation.scope_version == 2 and conversation.owner_user_id == int(user["id"])
        assert run.checkpoint["purpose"] == "assistant_chat"
        assert run.checkpoint["authorization"]["mode"] == "discuss"
        assert run.checkpoint["authorization"]["steps"] == []
        assert run.config_snapshot["model_key"] == "agnes-3.0-flash"
        assert len(turn.request_messages) == 2
        assert SOURCE in turn.request_messages[0]["kwargs"]["instructions"]
        assert turn.request_messages[0]["kwargs"]["user_prompt"] == PROMPTS[0]
        assert (turn.id >> 12) & 1023 == supervisor["executor_worker_id"]
        raw = (turn.response or {}).get("raw")
        if mode == "stop-after-response":
            assert browser["stop_posts"] == 1 and run.cancel_requested
            assert turn.status == "succeeded" and raw
            assert turn.response["applied"] is False
            assert "红色" in turn.response["normalized"]["output"]
            assert provider["replays"] == supervisor["recoveries"] == 0
            assert len(supervisor["children"]) == 1
        else:
            assert browser["stop_posts"] == 0
            assert supervisor["recoveries"] == 1
            assert supervisor["children"][0]["terminated"] is True
            assert supervisor["children"][0]["exit_code"] != 0
            assert (
                hashlib.sha256(
                    json.dumps(turn.request_messages, sort_keys=True).encode()
                ).hexdigest()
                == supervisor["request_sha256"]
            )
            assert (
                hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
                == (supervisor["raw_sha256"])
            )
            if mode == "recover-after-response":
                assert raw and turn.status == "succeeded" and turn.response["applied"] is True
                assert provider["replays"] == 1 and len(supervisor["children"]) == 2
            else:
                assert turn.status == "unknown" and raw is None
                assert run.error == {"code": "agent_acceptance_unknown"}
                assert provider["replays"] == 0 and len(supervisor["children"]) == 1
        messages = list(
            session.scalars(
                select(AgentMessage)
                .where(AgentMessage.conversation_id == conversation_id)
                .order_by(AgentMessage.seq)
            )
        )
        roles = ["user"] if mode == "stop-after-response" else ["user", "assistant"]
        assert [row.role for row in messages] == roles
        assert messages[0].content == PROMPTS[0]
        if mode == "recover-after-response":
            assert "红色" in messages[1].content and "礼盒" in messages[1].content
        elif mode == "interrupt-after-headers":
            assert messages[1].content == UNKNOWN_MESSAGE
            assert messages[1].references == messages[1].artifacts == []
            assert provider["results"] == []
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentEvent)
                    .where(
                        AgentEvent.run_id == identifier, AgentEvent.event_type == "assistant.delta"
                    )
                )
                == 0
            )
        for model_type, predicate in (
            (AgentToolCall, AgentToolCall.run_id == identifier),
            (AgentArtifact, AgentArtifact.project_id == int(project["id"])),
            (AsyncTask, AsyncTask.project_id == int(project["id"])),
        ):
            assert (
                session.scalar(select(func.count()).select_from(model_type).where(predicate)) == 0
            )
        assert_no_secret(run.checkpoint, secret)
        assert_no_secret(turn.request_messages, secret)
        assert_no_secret([row.content for row in messages], secret)
    conversation_path = "/api/v1/assistant/conversations/" + str(conversation_id)
    for other in (member, outsider):
        for suffix in ("", "/messages", "/state", "/events"):
            assert other.get(conversation_path + suffix).status_code == 404
        assert other.get("/api/v1/assistant/runs/" + str(identifier)).status_code == 404
        assert other.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
    if surface == "canvas":
        current = owner.get(path + "/my-document").json()["source_document"]
        shared = member.get(path + "/my-document").json()["source_document"]
        for field in ("revision", "nodes", "connections"):
            assert current[field] == original[field]
        assert shared["nodes"][0]["metadata"]["content"] == SOURCE
    else:
        assert owner.get(path + "/writing").json() == original
        assert member.get(path + "/writing").json() == original
    for value in (browser, provider, supervisor, shared if surface == "canvas" else original):
        assert_no_secret(value, secret)
    (directory / "acceptance.json").write_text(
        json.dumps(
            {
                "protocol": protocol,
                "surface": surface,
                "input_mode": mode,
                "width": browser["width"],
                "browser": browser,
                "provider": provider,
                "supervisor": supervisor,
                "python_postchecks": "passed",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("protocol", ["chat-completion", "openai-response"])
@pytest.mark.parametrize("surface", ["canvas", "standard"])
@pytest.mark.parametrize("input_mode", ["source", "image-skill", *LIFECYCLE_MODES])
def test_project_chat_real_agnes_original_ui_history_and_private_recovery(
    resource_app, tmp_path, protocol, width, surface, input_mode
):
    app, factory, original_settings = resource_app
    lifecycle = input_mode in LIFECYCLE_MODES
    app.state.settings = original_settings.model_copy(update={"agent_enabled": True})
    prompts = [IMAGE_PROMPT, PROMPTS[1]] if input_mode == "image-skill" else PROMPTS
    expected_color = "蓝色" if input_mode == "image-skill" else "红色"
    image_data = None
    if input_mode == "image-skill":
        image_path = Path(os.environ.get("AGNES_ASSISTANT_REFERENCE_IMAGE", ""))
        assert image_path.is_file(), "Provide the accepted blue gift image for live visual chat"
        image_data = image_path.read_bytes()
        (tmp_path / "reference.png").write_bytes(image_data)
        (tmp_path / "图片解读.md").write_text(SKILL_SOURCE, encoding="utf-8", newline="\n")
    suffix = surface[:1] + ("chat" if protocol == "chat-completion" else "responses") + str(width)
    owner, user = account(resource_app, "al_o_" + suffix)
    member, member_user = account(resource_app, "al_m_" + suffix)
    outsider, _ = account(resource_app, "al_x_" + suffix)
    if surface == "canvas":
        project, path, document = canvas(owner, "assistant-agnes-" + uuid4().hex)
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
        created_episode = owner.post(
            f"/api/v1/projects/{project['id']}/episodes", json={"title": "礼盒记录"}
        )
        assert created_episode.status_code == 201
        source_key, context_kind = created_episode.json()["id"], "episode"
        path = f"/api/v1/projects/{project['id']}/episodes/{source_key}"
        writing = owner.get(path + "/writing").json()
        saved = owner.put(
            path + "/novel",
            json={"content_version": writing["content_version"], "content": SOURCE},
        )
        assert saved.status_code == 200
        original = owner.get(path + "/writing").json()
    join(resource_app, owner, member, project["id"], member_user["id"])
    secret = live_key()
    created = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "Agnes助手-" + protocol,
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
    browser_port = int(os.environ.get("AGNES_ASSISTANT_BROWSER_PORT", "4192"))
    assert 1024 <= browser_port <= 65535
    executor_worker_id = int(os.environ.get("AGNES_ASSISTANT_EXECUTOR_WORKER_ID", "994"))
    assert 0 <= executor_worker_id <= 1023
    assert executor_worker_id != app.state.settings.snowflake_worker_id
    settings = app.state.settings.model_copy(
        update={"public_origin": f"http://127.0.0.1:{browser_port}"}
    )
    if lifecycle:
        settings = settings.model_copy(update={"agent_lease_seconds": 150})
    stopped = threading.Event()
    gateway = LiveAssistantGateway(settings, tmp_path, stopped)
    runtime = AgentRuntime(factory, settings, gateway)
    worker_errors = []

    def work():
        try:
            if lifecycle:
                supervise_lifecycle(
                    factory,
                    settings.model_copy(update={"snowflake_worker_id": executor_worker_id}),
                    tmp_path,
                    int(user["id"]),
                    input_mode,
                    stopped,
                )
                return
            while not stopped.wait(0.05):
                with factory() as session:
                    pending = session.execute(
                        select(AgentRun.id, AgentRun.message_version).where(
                            AgentRun.initiated_by == int(user["id"]),
                            AgentRun.status.in_({"queued", "running"}),
                            AgentRun.message_status == "pending",
                            AgentRun.next_run_at <= utcnow(),
                        )
                    ).all()
                for identifier, version in pending:
                    if not stopped.is_set():
                        runtime.execute_one(identifier, version)
        except BaseException as error:
            worker_errors.append(type(error).__name__)

    config = {
        "username": user["username"],
        "password": PASSWORD,
        "projectId": project["id"],
        "sourceKey": source_key,
        "surface": surface,
        "contextKind": context_kind,
        "inputMode": input_mode,
        "lifecycleMode": input_mode if lifecycle else None,
        "checkpointDirectory": str(tmp_path),
        "unknownMessage": UNKNOWN_MESSAGE,
        "expectedColor": expected_color,
        "imagePath": str(tmp_path / "reference.png") if image_data else None,
        "skillPath": str(tmp_path / "图片解读.md") if image_data else None,
        "skillUpdated": SKILL_UPDATED,
        "modelId": model["id"],
        "protocol": protocol,
        "width": width,
        "browserPort": browser_port,
        "prompts": prompts,
        "releasePath": str(tmp_path / "allow-provider"),
        "outputDirectory": str(tmp_path / "browser"),
    }
    with assistant_api(factory, settings, app.state.storage) as url:
        config["apiUrl"] = url
        worker = threading.Thread(target=work, name="assistant-live-executor", daemon=True)
        worker.start()
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-project-assistant-agnes-python.mjs"],
            cwd=Path(__file__).resolve().parents[3] / "frontend" / "canvas",
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
            pytest.exit("Live assistant timed out; reconcile saved runs before another POST", 1)
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            (tmp_path / "allow-provider").touch(exist_ok=True)
            worker.join(25 if lifecycle else 10)
            if lifecycle:
                gateway.report = json.loads((tmp_path / "provider-report.json").read_text("utf-8"))
            else:
                gateway.checkpoint()
        assert not worker.is_alive()
        assert not worker_errors, worker_errors
        assert_no_secret(errors + output, secret)
        if process.returncode:
            pytest.exit("Live assistant requires reconciliation: " + errors + "\n" + output, 1)
    browser = json.loads(output)
    assert browser["step"] == "complete"
    if lifecycle:
        assert_lifecycle_postchecks(
            factory,
            settings,
            tmp_path,
            browser,
            gateway.report,
            input_mode,
            owner,
            member,
            outsider,
            user,
            project,
            model,
            path,
            original,
            surface,
            protocol,
            secret,
        )
        return
    assert browser["message_posts"] == gateway.report["submissions"] == 2
    assert len(gateway.report["requests"]) == 2
    expected_endpoint = "/v1/chat/completions" if protocol == "chat-completion" else "/v1/responses"
    assert all(
        item["method"] == "POST"
        and item["host"] == "api.agnes-ai.cn"
        and item["path"] == expected_endpoint
        and item["http_status"] == 200
        and item["options"]["stream"] is True
        for item in gateway.report["requests"]
    )
    conversation_id = int(browser["conversation_id"])
    run_ids = [int(identifier) for identifier in browser["run_ids"]]
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
        source = runs[0].checkpoint["context_snapshot"]
        assert source["kind"] == context_kind and source["id"] == source_key
        assert SOURCE in json.dumps(source, ensure_ascii=False)
        turns = list(session.scalars(select(AgentTurn).where(AgentTurn.run_id.in_(run_ids))))
        assert len(turns) == 2 and all(row.request_messages and row.response for row in turns)
        first_turn, second_turn = sorted(turns, key=lambda row: run_ids.index(row.run_id))
        first_input = first_turn.request_messages[0]["kwargs"]
        second_input = second_turn.request_messages[0]["kwargs"]
        assert SOURCE in first_input["instructions"]
        if image_data:
            attachment = session.get(AgentAttachment, int(browser["attachment_id"]))
            assert attachment.kind == "image" and attachment.owner_user_id == int(user["id"])
            assert attachment.conversation_id == conversation_id
            assert attachment.attached_message_id is not None
            assert attachment.checksum_sha256 == hashlib.sha256(image_data).hexdigest()
            media = session.get(MediaFile, attachment.media_id)
            assert media.scope_user_id == int(user["id"]) and media.project_id is None
            assert media.checksum_sha256 == attachment.checksum_sha256
            location = ObjectLocation.parse(media.storage_locator, {settings.minio_image_bucket})
            with app.state.storage.open(location.bucket, location.object_name) as stream:
                assert stream.read() == image_data
            assert len(browser["attachment_views"]) == 3
            assert all(
                item["sha256"] == attachment.checksum_sha256 for item in browser["attachment_views"]
            )
            user_prompt = first_input["user_prompt"]
            assert user_prompt[0] == prompts[0]
            binary = [item for item in user_prompt if isinstance(item, dict)]
            assert len(binary) == 1 and binary[0]["media_type"] == "image/jpeg"
            processed_hash = hashlib.sha256(base64.urlsafe_b64decode(binary[0]["data"])).hexdigest()
            assert inline_image_hashes(first_turn.request_messages[1]) == [processed_hash]
            assert browser["skill_version_changed_before_provider_post"] is True
            skill = session.get(AgentSkill, int(browser["skill_id"]))
            assert skill.content_version == 2 and skill.instructions == SKILL_UPDATED
            first_run = next(row for row in runs if row.id == run_ids[0])
            frozen_skill = first_run.checkpoint["selected_skills"]
            assert len(frozen_skill) == 1
            assert frozen_skill[0]["content_version"] == "1"
            assert frozen_skill[0]["instructions"] == SKILL_SOURCE
            first_source = json.loads(first_input["instructions"].split("本次资料：", 1)[1])
            assert first_source["skills"] == frozen_skill
        else:
            assert first_input["user_prompt"] == prompts[0]
        assert second_input["user_prompt"] == prompts[1]
        assert (
            next(row for row in runs if row.id == run_ids[1]).checkpoint.get("context_snapshot")
            is None
        )
        second_source = json.loads(second_input["instructions"].split("本次资料：", 1)[1])
        assert second_source["source"] is None
        assert [item["role"] for item in second_source["history"]] == ["user", "assistant"]
        assert second_source["history"][0]["content"] == prompts[0]
        assert expected_color in second_source["history"][1]["content"]
        assert "礼盒" in second_source["history"][1]["content"]
        assert second_source["skills"] == []
        assert inline_image_hashes(second_turn.request_messages[1]) == []
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
        assert expected_color in messages[1].content and "礼盒" in messages[1].content
        assert "礼盒" in messages[3].content
        assert_no_secret([row.content for row in messages], secret)
    conversation_path = "/api/v1/assistant/conversations/" + str(conversation_id)
    assert owner.get(conversation_path).status_code == 200
    for other in (member, outsider):
        for suffix in ("", "/messages", "/state", "/events", "/attachments"):
            assert other.get(conversation_path + suffix).status_code == 404
        for identifier in run_ids:
            assert other.get("/api/v1/assistant/runs/" + str(identifier)).status_code == 404
        assert other.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
        if image_data:
            assert other.get("/api/v1/agent/skills/" + browser["skill_id"]).status_code == 404
    assert (
        member.get("/api/v1/assistant/conversations", params={"project_id": project["id"]}).json()[
            "total"
        ]
        == 0
    )
    if surface == "canvas":
        current = owner.get(path + "/my-document").json()["source_document"]
        for field in ("revision", "nodes", "connections"):
            assert current[field] == original[field]
        shared = member.get(path + "/my-document").json()["source_document"]
        assert shared["nodes"][0]["metadata"]["content"] == SOURCE
    else:
        current = owner.get(path + "/writing").json()
        shared = member.get(path + "/writing").json()
        assert current == shared == original
        assert shared["novel"]["content"] == SOURCE
    assert_no_secret(shared, secret)
    assert_no_secret(gateway.report, secret)
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "protocol": protocol,
                "surface": surface,
                "input_mode": input_mode,
                "width": width,
                "browser": browser,
                "provider": gateway.report,
                "python_postchecks": "passed",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
