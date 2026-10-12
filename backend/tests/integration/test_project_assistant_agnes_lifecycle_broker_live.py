"""原助手界面、正式 Scheduler/Agent Worker/AMQP 与真实 Agnes 受理后生命周期。"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from short_drama.agent.publisher import AgentRabbitSender
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
from short_drama.service.base import utcnow
from tests.integration.test_canvas_agnes_live import assert_no_secret, live_key
from tests.integration.test_canvas_browser import stop_browser
from tests.integration.test_canvas_generation_broker import eventually, worker_environment
from tests.integration.test_canvas_resources import canvas
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.integration.test_identity_collaboration import project as standard_project
from tests.integration.test_project_assistant_agnes_broker_live import (
    ROLE_IDS,
    ROOT,
    official_stack,
    read_json,
)
from tests.integration.test_project_assistant_agnes_broker_live import pytestmark as pytestmark
from tests.integration.test_project_assistant_agnes_live import (
    LIFECYCLE_MODES,
    PROMPTS,
    SOURCE,
    UNKNOWN_MESSAGE,
)


@contextmanager
def observe_formal_lifecycle(factory, settings, directory, owner_id, mode, stack, workers):
    """仅中止自己创建的 Agent 进程并重新订阅；恢复和再次发布由原 Scheduler 负责。"""
    stopped = threading.Event()
    failures, logs = [], []
    initial_worker = workers["agent"]
    initial_report = read_json(directory / "worker-report.json")
    assert initial_report is not None
    assert initial_worker.pid in {initial_report["pid"], initial_report["parent_pid"]}
    audit = {
        "scope": "formal Scheduler/Agent Worker/AMQP lifecycle",
        "lease_seconds": settings.agent_lease_seconds,
        "executor_worker_id": ROLE_IDS["agent"],
        "children": [{"pid": initial_report["pid"], "parent_pid": initial_worker.pid}],
        "recoveries": 0,
        "manual_runtime_calls": 0,
        "manual_recovery_calls": 0,
    }

    def checkpoint():
        (directory / "supervisor-report.json").write_text(
            json.dumps(audit, indent=2), encoding="utf-8"
        )

    def supervise():
        interrupted = terminal_recorded = False
        lease_until = None
        try:
            while not stopped.wait(0.05):
                if (directory / "interrupt-worker").exists() and not interrupted:
                    assert mode != "stop-after-response"
                    with factory() as session:
                        run = session.scalar(
                            select(AgentRun).where(AgentRun.initiated_by == owner_id)
                        )
                        turn = session.scalar(select(AgentTurn).where(AgentTurn.run_id == run.id))
                        assert run.status == "running" and turn.status == "sent"
                        raw = (turn.response or {}).get("raw")
                        assert bool(raw) is (mode == "recover-after-response")
                        lease_until = run.lease_until
                        assert lease_until is not None and lease_until > utcnow()
                        audit.update(
                            {
                                "run_id": str(run.id),
                                "message_version": run.message_version,
                                "lease_until_before_kill": lease_until.isoformat(),
                                "request_sha256": hashlib.sha256(
                                    json.dumps(turn.request_messages, sort_keys=True).encode()
                                ).hexdigest(),
                                "raw_sha256": hashlib.sha256(
                                    json.dumps(raw, sort_keys=True).encode()
                                ).hexdigest(),
                            }
                        )
                    observed = read_json(directory / "provider-report.json")
                    assert observed["provider_headers"]["status"] == 200
                    assert observed["submissions"] == 1
                    assert initial_worker.poll() is None
                    stop_browser(initial_worker)
                    initial_worker.wait(10)
                    assert initial_worker.poll() is not None
                    audit["children"][0].update(
                        terminated=True, exit_code=initial_worker.returncode
                    )
                    audit["killed_at"] = utcnow().isoformat()
                    environment = worker_environment(settings, factory)
                    environment["SNOWFLAKE_WORKER_ID"] = str(ROLE_IDS["agent"])
                    environment["PYTHONIOENCODING"] = "utf-8"
                    environment["SMTP_HOST"] = environment["SMTP_FROM"] = ""
                    output = (directory / "agent-replacement.log").open("w", encoding="utf-8")
                    logs.append(output)
                    replacement = subprocess.Popen(
                        [sys.executable, *stack["roles"]["agent"]["arguments"]],
                        cwd=ROOT / "backend",
                        env=environment,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        start_new_session=os.name != "nt",
                    )
                    workers["agent"] = replacement
                    stack["roles"]["agent"]["replacement_pid"] = replacement.pid
                    audit["children"].append({"parent_pid": replacement.pid})
                    interrupted = True
                    checkpoint()
                if not (directory / "supplier-ready").exists():
                    continue
                with factory() as session:
                    run = session.scalar(select(AgentRun).where(AgentRun.initiated_by == owner_id))
                    current_version, current_status = run.message_version, run.status
                if interrupted:
                    current_worker = read_json(directory / "worker-report.json")
                    if current_worker and current_worker["parent_pid"] == workers["agent"].pid:
                        audit["children"][1]["pid"] = current_worker["pid"]
                    if current_version > audit["message_version"] and not audit["recoveries"]:
                        assert utcnow() >= lease_until
                        audit.update(recoveries=1, recovered_at=utcnow().isoformat())
                        checkpoint()
                if current_status in {"succeeded", "failed", "cancelled"} and not terminal_recorded:
                    current_worker = read_json(directory / "worker-report.json")
                    if (
                        current_worker
                        and current_worker["deliveries"]
                        and all(
                            row["completed"] or row.get("worker_lost")
                            for row in current_worker["deliveries"]
                        )
                    ):
                        audit["terminal_status"] = current_status
                        (directory / "executor-finished").touch()
                        terminal_recorded = True
                        checkpoint()
        except BaseException as error:
            failures.append(error)
            (directory / "supervisor-error.json").write_text(
                json.dumps({"type": type(error).__name__}), encoding="utf-8"
            )

    checkpoint()
    thread = threading.Thread(target=supervise, name="formal-agent-lifecycle-observer", daemon=True)
    thread.start()
    try:
        yield audit, failures
    finally:
        stopped.set()
        thread.join(10)
        assert not thread.is_alive()
        for output in logs:
            output.close()
        checkpoint()


def assert_formal_lifecycle_postchecks(
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
                assert provider["replays"] == 0 and len(supervisor["children"]) == 2
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
                "stack": read_json(directory / "stack-report.json"),
                "broker_deliveries": read_json(directory / "worker-report.json"),
                "python_postchecks": "passed",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("protocol", ["chat-completion", "openai-response"])
@pytest.mark.parametrize("surface", ["canvas", "standard"])
@pytest.mark.parametrize("mode", LIFECYCLE_MODES)
def test_original_assistant_real_agnes_lifecycle_through_formal_broker(
    resource_app, tmp_path, monkeypatch, mode, surface, protocol, width
):
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    monkeypatch.setenv("AGNES_ASSISTANT_BROKER_LIFECYCLE_MODE", mode)
    app, factory, current_settings = resource_app
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    app.state.settings = current_settings.model_copy(update={"agent_enabled": True})
    suffix = (
        mode[0]
        + surface[:1]
        + ("chat" if protocol == "chat-completion" else "responses")
        + str(width)
    )
    owner, user = account(resource_app, "al_o_" + suffix)
    member, member_user = account(resource_app, "al_m_" + suffix)
    outsider, _ = account(resource_app, "al_x_" + suffix)
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
        response = owner.post(
            f"/api/v1/projects/{project['id']}/episodes", json={"title": "礼盒记录"}
        )
        assert response.status_code == 201
        source_key, context_kind = response.json()["id"], "episode"
        path = f"/api/v1/projects/{project['id']}/episodes/{source_key}"
        writing = owner.get(path + "/writing").json()
        saved = owner.put(
            path + "/novel", json={"content_version": writing["content_version"], "content": SOURCE}
        )
        assert saved.status_code == 200
        original = owner.get(path + "/writing").json()
    join(resource_app, owner, member, project["id"], member_user["id"])
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
            "agent_lease_seconds": 150,
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
        "inputMode": mode,
        "lifecycleMode": mode,
        "checkpointDirectory": str(tmp_path),
        "unknownMessage": UNKNOWN_MESSAGE,
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
        with observe_formal_lifecycle(
            factory, settings, tmp_path, int(user["id"]), mode, stack, workers
        ) as (supervisor, failures):
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
            pending_input = json.dumps(config)
            deadline = time.monotonic() + 420
            try:
                while True:
                    try:
                        output, errors = process.communicate(pending_input, timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        pending_input = None
                        if failures:
                            raise failures[0] from None
                        if time.monotonic() >= deadline:
                            pytest.exit(
                                "Formal lifecycle timed out; reconcile original request first", 1
                            )
            finally:
                if process.poll() is None:
                    stop_browser(process)
                    process.communicate(timeout=10)
            assert_no_secret(output + errors, secret)
            if process.returncode:
                pytest.exit(
                    "Formal lifecycle requires reconciliation: " + errors + "\n" + output, 1
                )
            assert not failures
            browser = json.loads(output)
            assert browser["step"] == "complete"
            assert not browser["page_errors"] and not browser["http_errors"]
            deliveries = eventually(
                lambda: read_json(tmp_path / "worker-report.json"),
                lambda value: (
                    value is not None
                    and all(
                        row["completed"] or row.get("worker_lost") for row in value["deliveries"]
                    )
                ),
            )
            rows = deliveries["deliveries"]
            normal_count = len(rows)
            expected_count = {
                "stop-after-response": 1,
                "recover-after-response": 3,
                "interrupt-after-headers": 2,
            }[mode]
            assert normal_count == expected_count
            run_id, original_version = int(browser["run_ids"][0]), int(rows[0]["message_version"])
            for row in rows:
                assert int(row["run_id"]) == run_id
                assert row["delivery"]["exchange"] == settings.generation_queue_namespace + ".tasks"
                assert row["delivery"]["routing_key"] == "agent"
                if row["completed"]:
                    assert row["state"] == "SUCCESS"
            assert rows[0]["delivery"]["redelivered"] is False
            if mode == "stop-after-response":
                assert rows[0]["completed"] and len(deliveries["executions"]) == 1
            else:
                assert not rows[0]["completed"] and rows[0]["worker_lost"]
                assert rows[1]["completed"] and rows[1]["delivery"]["redelivered"] is True
                assert rows[1]["task_id"] == rows[0]["task_id"]
                assert rows[1]["message_version"] == rows[0]["message_version"]
                assert len(deliveries["executions"]) == 2
                assert rows[1]["worker_pid"] != rows[0]["worker_pid"]
                if mode == "recover-after-response":
                    assert int(rows[2]["message_version"]) == original_version + 1
                    assert rows[2]["delivery"]["redelivered"] is False and rows[2]["completed"]
            stack["roles"]["agent"]["execution_pids"] = [
                row["pid"] for row in deliveries["executions"]
            ]
            sender = AgentRabbitSender(settings)
            try:
                sender(run_id, original_version)
                sender(run_id, original_version - 1)
            finally:
                sender.app.close()
            deliveries = eventually(
                lambda: read_json(tmp_path / "worker-report.json"),
                lambda value: (
                    value is not None
                    and len(value["deliveries"]) == normal_count + 2
                    and all(
                        row["completed"] or row.get("worker_lost") for row in value["deliveries"]
                    )
                ),
            )
            assert all(
                row["completed"] and row["state"] == "SUCCESS"
                for row in deliveries["deliveries"][-2:]
            )
            provider = read_json(tmp_path / "provider-report.json")
            assert provider["submissions"] == 1
            assert provider["original_worker_pid"] == rows[0]["worker_pid"]
            assert provider["worker_pid"] == deliveries["pid"]
            assert provider["snowflake_worker_id"] == ROLE_IDS["agent"]
            assert all(worker.poll() is None for worker in workers.values())
    assert all(stack["cleanup"].values())
    supervisor["all_children_stopped"] = all(
        worker.poll() is not None for worker in workers.values()
    )
    (tmp_path / "supervisor-report.json").write_text(
        json.dumps(supervisor, indent=2), encoding="utf-8"
    )
    assert supervisor["manual_runtime_calls"] == supervisor["manual_recovery_calls"] == 0
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    assert_formal_lifecycle_postchecks(
        factory,
        settings,
        tmp_path,
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
    )
