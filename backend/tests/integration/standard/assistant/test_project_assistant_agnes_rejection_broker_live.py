"""显式授权：真实 Responses 图片拒绝、最终原UI反馈及正式AMQP恢复。"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from short_drama.agent.publisher import AgentRabbitSender
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
from short_drama.storage.models import ObjectLocation
from tests.integration.canvas.assistant.test_canvas_agnes_live import assert_no_secret, live_key
from tests.integration.canvas.document.test_canvas_browser import stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import eventually
from tests.integration.canvas.resources.test_canvas_resources import canvas
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.assistant.test_project_assistant_agnes_broker_live import (
    ROLE_IDS,
    ROOT,
    official_stack,
    read_json,
)
from tests.integration.standard.assistant.test_project_assistant_agnes_live import (
    IMAGE_PROMPT,
    SKILL_SOURCE,
    SKILL_UPDATED,
    SOURCE,
    inline_image_hashes,
)
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
        reason="Explicit real Agnes rejection, AMQP, isolated storage and browser opt-in required",
    ),
]
FAILURE_MESSAGE = "模型服务拒绝了当前任务，请检查模型配置或调整要求后继续。你的输入已保留。"
FAILURE_NOTICE = "这次回复未完成，已有消息仍保留。请核对模型和输入后继续。"


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("surface", ["canvas", "standard"])
def test_real_responses_image_rejection_final_ui_through_formal_eight_roles(
    resource_app, tmp_path, surface, width
):
    app, factory, current_settings = resource_app
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    assert not os.environ.get("AGNES_ASSISTANT_BROKER_LIFECYCLE_MODE")
    image_path = Path(os.environ.get("AGNES_ASSISTANT_REFERENCE_IMAGE", ""))
    assert image_path.is_file(), "Provide the accepted blue gift image for real rejection handling"
    image_data = image_path.read_bytes()
    image_hash = hashlib.sha256(image_data).hexdigest()
    assert image_hash == "a47ed24be0e913236bf292aba8b23d03cbe2feb7b33550eacf20fd04808f79b6"
    (tmp_path / "reference.png").write_bytes(image_data)
    (tmp_path / "图片解读.md").write_text(SKILL_SOURCE, encoding="utf-8", newline="\n")
    app.state.settings = current_settings.model_copy(update={"agent_enabled": True})
    suffix = surface[:1] + "responses" + str(width)
    owner, user = account(resource_app, "ar_o_" + suffix)
    foreign, _ = account(resource_app, "ar_m_" + suffix)
    outsider, _ = account(resource_app, "ar_x_" + suffix)
    if surface == "canvas":
        project, path, document = canvas(owner, "assistant-rejection-" + uuid4().hex)
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
            "name": "Agnes正式Responses图片拒绝助手",
            "service_type": "text",
            "provider": "Agnes",
            "model_key": "agnes-3.0-flash",
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": secret,
            "runtime_profile": {
                "version": 1,
                "api_format": "openai",
                "protocol": "openai-response",
            },
        },
    )
    if created.status_code != 201:
        pytest.fail("Saved real rejection model failed: HTTP " + str(created.status_code))
    model = created.json()
    assert_no_secret(model, secret)
    browser_port = int(os.environ.get("AGNES_ASSISTANT_BROKER_BROWSER_PORT", "4197"))
    namespace = "assistant_broker_live_" + uuid4().hex
    assert re.fullmatch(r"assistant_broker_live_[a-f0-9]{32}", namespace)
    settings = app.state.settings.model_copy(
        update={
            "public_origin": f"http://127.0.0.1:{browser_port}",
            "generation_queue_namespace": namespace,
            "smtp_host": "",
            "smtp_from": "",
            "render_scratch_root": str(tmp_path / "render-scratch"),
        }
    )
    config = {
        "username": user["username"],
        "password": PASSWORD,
        "projectId": project["id"],
        "sourceKey": source_key,
        "surface": surface,
        "contextKind": context_kind,
        "imagePath": str(tmp_path / "reference.png"),
        "skillPath": str(tmp_path / "图片解读.md"),
        "skillUpdated": SKILL_UPDATED,
        "failureMessage": FAILURE_MESSAGE,
        "failureNotice": FAILURE_NOTICE,
        "modelId": model["id"],
        "protocol": "openai-response",
        "width": width,
        "browserPort": browser_port,
        "prompt": IMAGE_PROMPT,
        "releasePath": str(tmp_path / "allow-provider"),
        "outputDirectory": str(tmp_path / "browser"),
    }
    with official_stack(factory, settings, tmp_path) as (url, stack, workers):
        config["apiUrl"] = url
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-project-assistant-agnes-rejection-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=360)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            process.communicate(timeout=10)
            pytest.exit("Real rejection UI timed out; reconcile before another POST", 1)
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert_no_secret(output + errors, secret)
        if process.returncode:
            pytest.exit("Real rejection UI requires reconciliation: " + errors + "\n" + output, 1)
        browser = json.loads(output)
        assert browser["step"] == "complete"
        assert not browser["page_errors"] and not browser["http_errors"]
        assert len(browser["run_ids"]) == 1
        run_id = int(browser["run_ids"][0])
        deliveries = eventually(
            lambda: read_json(tmp_path / "worker-report.json"),
            lambda value: (
                value is not None
                and len(value["deliveries"]) == 1
                and value["deliveries"][0]["completed"]
            ),
        )
        assert workers["agent"].pid in {deliveries["pid"], deliveries["parent_pid"]}
        stack["roles"]["agent"]["execution_pid"] = deliveries["pid"]
        original_delivery = deliveries["deliveries"][0]
        assert int(original_delivery["run_id"]) == run_id
        original_version = int(original_delivery["message_version"])
        with factory() as session:
            assert session.get(AgentRun, run_id).message_version == original_version + 1
        assert original_delivery["state"] == "SUCCESS"
        assert original_delivery["delivery"]["exchange"] == namespace + ".tasks"
        assert original_delivery["delivery"]["routing_key"] == "agent"
        assert original_delivery["delivery"]["redelivered"] is False
        # 终态原版本和过期版本只探测围栏，不应重新调用供应商。
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
                and len(value["deliveries"]) == 3
                and all(row["completed"] for row in value["deliveries"])
            ),
        )
        assert all(row["state"] == "SUCCESS" for row in deliveries["deliveries"])
        assert all(worker.poll() is None for worker in workers.values())
    provider = read_json(tmp_path / "provider-report.json")
    assert provider is not None
    assert provider["worker_pid"] == stack["roles"]["agent"]["execution_pid"]
    assert provider["snowflake_worker_id"] == ROLE_IDS["agent"]
    assert browser["message_posts"] == provider["submissions"] == 1
    assert browser["stop_posts"] == 0
    assert len(provider["requests"]) == 1
    assert provider["results"] == []
    assert provider["downloads"] == []
    assert provider["error"] == {
        "code": "agent_provider_rejected",
        "accepted_unknown": False,
        "http_status": 400,
    }
    request = provider["requests"][0]
    assert request["method"] == "POST" and request["host"] == "api.agnes-ai.cn"
    assert request["path"] == "/v1/responses" and request["http_status"] == 400
    assert request["options"]["stream"] is True
    assert all(stack["cleanup"].values())
    assert len(stack["roles"]) == len({row["pid"] for row in stack["roles"].values()}) == 8
    conversation_id = int(browser["conversation_id"])
    with factory() as session:
        conversation = session.get(AgentConversation, conversation_id)
        assert conversation.scope_version == 2 and conversation.owner_user_id == int(user["id"])
        runs = list(
            session.scalars(select(AgentRun).where(AgentRun.conversation_id == conversation_id))
        )
        assert len(runs) == 1 and runs[0].id == run_id
        run = runs[0]
        assert run.status == "failed" and run.error == {"code": "agent_provider_rejected"}
        assert run.finished_at is not None and run.message_status == "idle"
        assert run.lease_token is None and run.lease_until is None and run.next_run_at is None
        assert run.config_snapshot["model_key"] == "agnes-3.0-flash"
        assert run.budget["tool_calls"] == run.usage["tool_calls"] == 0
        assert run.checkpoint["purpose"] == "assistant_chat"
        assert run.checkpoint["authorization"]["mode"] == "discuss"
        assert run.checkpoint["authorization"]["steps"] == []
        assert_no_secret(run.checkpoint, secret)
        turns = list(session.scalars(select(AgentTurn).where(AgentTurn.run_id == run_id)))
        assert len(turns) == 1
        turn = turns[0]
        assert turn.status == "failed" and turn.error == run.error
        assert turn.finished_at is not None and len(turn.request_messages) == 2
        raw = (turn.response or {}).get("raw")
        assert raw and raw["codec"] == "agent.raw-response" and raw["status_code"] == 400
        assert base64.b64decode(raw["body_b64"], validate=True)
        assert not turn.response.get("applied")
        first_input = turn.request_messages[0]["kwargs"]
        assert SOURCE in first_input["instructions"]
        assert first_input["user_prompt"][0] == IMAGE_PROMPT
        binary = [item for item in first_input["user_prompt"] if isinstance(item, dict)]
        assert len(binary) == 1 and binary[0]["media_type"] == "image/jpeg"
        processed_hash = hashlib.sha256(base64.urlsafe_b64decode(binary[0]["data"])).hexdigest()
        assert inline_image_hashes(turn.request_messages[1]) == [processed_hash]
        attachment = session.get(AgentAttachment, int(browser["attachment_id"]))
        assert attachment.kind == "image" and attachment.owner_user_id == int(user["id"])
        assert attachment.conversation_id == conversation_id
        assert attachment.attached_message_id == run.trigger_message_id
        assert attachment.checksum_sha256 == image_hash
        media = session.get(MediaFile, attachment.media_id)
        assert media.scope_user_id == int(user["id"]) and media.project_id is None
        assert media.checksum_sha256 == image_hash
        location = ObjectLocation.parse(media.storage_locator, {settings.minio_image_bucket})
        with app.state.storage.open(location.bucket, location.object_name) as stream:
            assert stream.read() == image_data
        assert [item["phase"] for item in browser["attachment_views"]] == [
            "pending",
            "running-fresh",
            "terminal",
            "terminal-fresh",
        ]
        assert all(item["sha256"] == image_hash for item in browser["attachment_views"])
        assert browser["skill_version_changed_before_provider_post"] is True
        assert browser["cookies_only_running_recovery_before_provider_post"] is True
        assert browser["fresh_terminal_recovery_same_conversation"] is True
        assert len(browser["failure_views"]) == 2
        skill = session.get(AgentSkill, int(browser["skill_id"]))
        assert skill.content_version == 2 and skill.instructions == SKILL_UPDATED
        frozen_skill = run.checkpoint["selected_skills"]
        assert len(frozen_skill) == 1 and frozen_skill[0]["content_version"] == "1"
        assert frozen_skill[0]["instructions"] == SKILL_SOURCE
        first_source = json.loads(first_input["instructions"].split("本次资料：", 1)[1])
        assert first_source["skills"] == frozen_skill
        source = run.checkpoint["context_snapshot"]
        assert source["kind"] == context_kind and source["id"] == source_key
        assert SOURCE in json.dumps(source, ensure_ascii=False)
        events = list(session.scalars(select(AgentEvent).where(AgentEvent.run_id == run_id)))
        assert not any(row.event_type == "assistant.delta" for row in events)
        finished = [row for row in events if row.event_type == "run.finished"]
        assert len(finished) == 1 and finished[0].payload["status"] == "failed"
        assert finished[0].payload["error"] == run.error
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentToolCall)
                .where(AgentToolCall.run_id == run_id)
            )
            == 0
        )
        for model_type in (AgentArtifact, AsyncTask):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model_type)
                    .where(model_type.project_id == int(project["id"]))
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
        assert [row.role for row in messages] == ["user", "assistant"]
        assert messages[0].content == IMAGE_PROMPT and messages[1].content == FAILURE_MESSAGE
        assert messages[1].references == messages[1].artifacts == []
        assert_no_secret(turn.request_messages, secret)
        assert_no_secret(turn.response, secret)
        assert_no_secret([row.payload for row in events], secret)
    conversation_path = "/api/v1/assistant/conversations/" + str(conversation_id)
    assert owner.get(conversation_path).status_code == 200
    for other in (foreign, outsider):
        for suffix in ("", "/messages", "/state", "/events", "/attachments"):
            assert other.get(conversation_path + suffix).status_code == 404
        assert other.get("/api/v1/assistant/runs/" + str(run_id)).status_code == 404
        assert other.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
        assert other.get("/api/v1/agent/skills/" + browser["skill_id"]).status_code == 404
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
    for value in (visible, provider, browser, deliveries, stack):
        assert_no_secret(value, secret)
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    (tmp_path / "rejection-acceptance.json").write_text(
        json.dumps(
            {
                "scope": "real provider rejection final UI, not model image success",
                "surface": surface,
                "protocol": "openai-response",
                "width": width,
                "browser": browser,
                "provider": provider,
                "stack": stack,
                "broker_deliveries": deliveries,
                "python_postchecks": "passed",
                "model_image_success": False,
                "media_worker_business_paths": "not exercised",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
