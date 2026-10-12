"""显式授权：真实图片/Skill、原共用助手与正式八角色/AMQP；助手纯对话。"""

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
from tests.integration.test_canvas_agnes_live import assert_no_secret, live_key
from tests.integration.test_canvas_browser import stop_browser
from tests.integration.test_canvas_generation_broker import eventually
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
from tests.integration.test_project_assistant_agnes_live import (
    IMAGE_PROMPT,
    PROMPTS,
    SKILL_SOURCE,
    SKILL_UPDATED,
    SOURCE,
    inline_image_hashes,
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


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("protocol", ["chat-completion", "openai-response"])
@pytest.mark.parametrize("surface", ["canvas", "standard"])
def test_original_image_skill_assistant_real_agnes_through_formal_eight_roles(
    resource_app, tmp_path, surface, protocol, width
):
    app, factory, current_settings = resource_app
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    image_path = Path(os.environ.get("AGNES_ASSISTANT_REFERENCE_IMAGE", ""))
    assert image_path.is_file(), "Provide the accepted blue gift image for live visual chat"
    image_data = image_path.read_bytes()
    assert image_data
    (tmp_path / "reference.png").write_bytes(image_data)
    (tmp_path / "图片解读.md").write_text(SKILL_SOURCE, encoding="utf-8", newline="\n")
    prompts = [IMAGE_PROMPT, PROMPTS[1]]
    expected_color = "蓝色"
    app.state.settings = current_settings.model_copy(update={"agent_enabled": True})
    suffix = surface[:1] + ("chat" if protocol == "chat-completion" else "responses") + str(width)
    owner, user = account(resource_app, "ab_o_" + suffix)
    member, member_user = account(resource_app, "ab_m_" + suffix)
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
            "name": "Agnes正式图片助手-" + protocol,
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
        "inputMode": "image-skill",
        "expectedColor": expected_color,
        "imagePath": str(tmp_path / "reference.png"),
        "skillPath": str(tmp_path / "图片解读.md"),
        "skillUpdated": SKILL_UPDATED,
        "modelId": model["id"],
        "protocol": protocol,
        "width": width,
        "browserPort": browser_port,
        "prompts": prompts,
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
        assert inline_image_hashes(first.request_messages[1]) == [processed_hash]
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
        source = first_run.checkpoint["context_snapshot"]
        assert source["kind"] == context_kind and source["id"] == source_key
        assert SOURCE in json.dumps(source, ensure_ascii=False)
        assert (
            next(row for row in runs if row.id == run_ids[1]).checkpoint.get("context_snapshot")
            is None
        )
        assert second_input["user_prompt"] == prompts[1]
        assert inline_image_hashes(second.request_messages[1]) == []
        history = json.loads(second_input["instructions"].split("本次资料：", 1)[1])
        assert history["source"] is None and history["skills"] == []
        assert [row["role"] for row in history["history"]] == ["user", "assistant"]
        assert history["history"][0]["content"] == prompts[0]
        assert (
            expected_color in history["history"][1]["content"]
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
        assert current == shared == original and shared["novel"]["content"] == SOURCE
    assert_no_secret(shared, secret)
    assert_no_secret(provider, secret)
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "surface": surface,
                "input_mode": "image-skill",
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
