"""显式授权候选：原 Skill 连接点、正式八角色与真实 Agnes 纯文字单场。"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.core.crypto import KeyCipher
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskTextDelta,
    MediaFile,
)
from short_drama.domain.canvas import CanvasNode, CanvasNodeUserState
from short_drama.tasks.publisher import RabbitSender
from tests.integration.canvas.assistant.test_canvas_agnes_live import assert_no_secret, live_key
from tests.integration.canvas.document.test_canvas_browser import stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import eventually
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, WORKSPACE
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import canvas
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.assistant.test_project_assistant_agnes_broker_live import (
    ROLE_IDS,
    official_stack,
    read_json,
)
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_AGNES_LIVE",
                "RUN_AGNES_SKILL_BROKER_LIVE",
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
                "RUN_CANVAS_BROKER_INTEGRATION",
            )
        ),
        reason="Explicit Agnes Skill, formal AMQP, isolated storage and browser opt-in required",
    ),
]
ROOT = Path(__file__).resolve().parents[5]
MODEL = "agnes-3.0-flash"
PROTOCOL = "chat-completion"
PROMPT = "请按所连接的私人模板写一段短剧镜头正文。"
SNAPSHOT = {
    "id": "agnes-skill-private-template",
    "name": "私人站台镜头模板",
    "category": "storyboard",
    "outputMode": "text",
    "description": "只使用本次已保存的站台道具设定。",
    "template": (
        "写出旅客在站台的一个具体动作，正文中必须逐字包含“清晨车站”和“红色礼盒”。"
        "内容是人工构造的短剧测试场景。"
    ),
    "outputContract": "仅输出30至80字的中文镜头正文，不加标题、解释、列表或代码块。",
    "version": 3,
    "tags": ["分镜", "私人模板"],
}
SKILL_INPUT = "\n\n".join(
    [
        "【技能：" + SNAPSHOT["name"] + "】",
        "用途：" + SNAPSHOT["description"],
        "执行模板：\n" + SNAPSHOT["template"],
        "输出约束：\n" + SNAPSHOT["outputContract"],
        "请严格执行该技能，只输出结果，不要输出解释性套话。",
    ]
)
EXPECTED_PROMPT = PROMPT + "\n\n" + SKILL_INPUT


@pytest.mark.parametrize(("width", "variant"), [(1440, "snapshot")])
def test_original_skill_real_agnes_through_formal_generation_worker(
    resource_app, database_errors, tmp_path, monkeypatch, width, variant
):
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    node = shutil.which("node")
    assert node, "Node.js is required for the explicit Agnes Skill browser integration"
    suffix = uuid4().hex[:12]
    owner, owner_user = account(resource_app, "ags_o_" + suffix)
    foreign, foreign_user = account(resource_app, "ags_m_" + suffix)
    outsider, outside_user = account(resource_app, "ags_x_" + suffix)
    project, path, document = canvas(owner, "agnes-skill-" + uuid4().hex)

    private = {"content": "作者私人正文回退", "skillSnapshot": deepcopy(SNAPSHOT)}
    updated_private = deepcopy(private)
    updated_private["skillSnapshot"].update(
        template="后续任务改为深夜机场与蓝色行李箱，不能替换本次已受理提示词。", version=4
    )
    document["nodes"] = [
        {
            "id": "skill-input",
            "type": "skill",
            "title": "技能引用",
            "position": {"x": -160, "y": -150},
            "width": 320,
            "height": 280,
            "metadata": private,
        }
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "agnes-skill-existing-snapshot-" + suffix},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    document = owner.get(path + "/my-document").json()["source_document"]
    secret = live_key()
    created = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "Agnes私人Skill正式验收",
            "service_type": "text",
            "provider": "Agnes",
            "model_key": MODEL,
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": secret,
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": PROTOCOL},
        },
    )
    if created.status_code != 201:
        pytest.fail("Saved real Skill model failed: HTTP " + str(created.status_code))
    model = created.json()
    assert_no_secret(model, secret)
    selection = f"host-{model['id']}::{MODEL}"
    workspace = owner.get(WORKSPACE).json()
    selected = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "model": selection,
                "textModel": selection,
                "count": "1",
            },
        },
    )
    assert selected.status_code == 200, selected.text
    assert_no_secret(selected.json(), secret)
    factory = resource_app[1]
    with factory() as session:
        saved_model = session.get(AIModelConfig, int(model["id"]))
        assert saved_model.apikey != secret
        if (
            KeyCipher(resource_app[2].encryption_key.get_secret_value()).decrypt(saved_model.apikey)
            != secret
        ):
            raise AssertionError("Saved model credential does not match the authorized key")
    browser_port = int(os.environ.get("AGNES_SKILL_BROKER_BROWSER_PORT", "4199"))
    namespace = "assistant_broker_live_" + uuid4().hex
    assert re.fullmatch(r"assistant_broker_live_[a-f0-9]{32}", namespace)
    settings = resource_app[2].model_copy(
        update={
            "public_origin": f"http://127.0.0.1:{browser_port}",
            "generation_text_budget_seconds": 120,
            "agent_enabled": True,
            "generation_queue_namespace": namespace,
            "smtp_host": "",
            "smtp_from": "",
            "render_scratch_root": str(tmp_path / "render-scratch"),
        }
    )
    monkeypatch.setenv("AGNES_GENERATION_BROKER_DIRECTORY", str(tmp_path))
    config = {
        "username": owner_user["username"],
        "foreignUsername": foreign_user["username"],
        "outsideUsername": outside_user["username"],
        "password": PASSWORD,
        "projectId": project["id"],
        "canvasId": project["primary_canvas_id"],
        "sourceKey": document["id"],
        "prompt": PROMPT,
        "expectedPrompt": EXPECTED_PROMPT,
        "privateMetadata": private,
        "updatedPrivateMetadata": updated_private,
        "model": MODEL,
        "modelId": model["id"],
        "protocol": PROTOCOL,
        "width": width,
        "variant": variant,
        "browserPort": browser_port,
        "releasePath": str(tmp_path / "real-provider-submit-allowed"),
        "providerReportPath": str(tmp_path / "provider-report.json"),
        "outputDirectory": str(tmp_path / "browser"),
    }
    with official_stack(
        factory,
        settings,
        tmp_path,
        generation_observer_module="tests.support.integration.agnes_generation_broker_observer",
    ) as (api_url, stack, processes):
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [node, "scripts/verify-agnes-skill-python.mjs"],
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
            pytest.exit(
                "Formal Skill timed out; reconcile original identity before another POST", 1
            )
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert_no_secret(output + errors, secret)
        provider = read_json(tmp_path / "provider-report.json")
        browser_source = "stdout"
        try:
            browser = json.loads(output)
        except (TypeError, ValueError):
            browser_source = "archived_report"
            try:
                browser = read_json(tmp_path / "browser/report.json")
            except (OSError, ValueError):
                browser = None
        if not isinstance(browser, dict):
            browser_source = "unavailable"
            browser = {}
        if process.returncode or not browser:
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            observed_tasks = owner.get(TASKS, params={"projectId": document["id"]})
            task_rows = observed_tasks.json() if observed_tasks.status_code == 200 else None
            reconciliation = {
                "process_returncode": process.returncode,
                "browser_report_source": browser_source,
                "task_lookup_http_status": observed_tasks.status_code,
                "task_id_from_browser": browser.get("task_id"),
                "task_states": [
                    {
                        key: row.get(key)
                        for key in ("id", "status", "errorCode", "canRetry", "canResume")
                    }
                    for row in task_rows
                ]
                if isinstance(task_rows, list)
                else None,
                "provider_submissions": provider.get("submissions") if provider else None,
                "provider_requests": len(provider.get("requests", [])) if provider else None,
                "new_submission_performed": False,
            }
            assert_no_secret(reconciliation, secret)
            (tmp_path / "reconciliation.json").write_text(
                json.dumps(reconciliation, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if provider is not None and provider["submissions"] == 0 and not provider["requests"]:
                pytest.fail("Formal Skill failed before provider POST: " + errors + "\n" + output)
            if provider is None and task_rows == []:
                pytest.fail("Formal Skill failed before admission: " + errors + "\n" + output)
            pytest.exit(
                "Formal Skill requires reconciliation without another POST: "
                + json.dumps(reconciliation, ensure_ascii=False)
                + "\n"
                + errors
                + "\n"
                + output,
                1,
            )
        assert provider is not None
        assert browser["step"] == "complete"
        task_id = int(browser["task_id"])
        deliveries = eventually(
            lambda: read_json(tmp_path / "generation-worker-report.json"),
            lambda value: (
                value is not None
                and len(value["deliveries"]) == 1
                and all(row["completed"] for row in value["deliveries"])
            ),
        )
        assert processes["text"].pid in {deliveries["pid"], deliveries["parent_pid"]}
        stack["roles"]["text"]["execution_pid"] = deliveries["pid"]
        assert provider["worker_pid"] == deliveries["pid"]
        assert provider["snowflake_worker_id"] == ROLE_IDS["text"]
        normal_deliveries = deepcopy(deliveries["deliveries"])
        row = normal_deliveries[0]
        assert int(row["task_id"]) == task_id and row["state"] == "SUCCESS"
        assert row["delivery"]["exchange"] == namespace + ".tasks"
        assert row["delivery"]["routing_key"] == "text"
        assert row["delivery"]["redelivered"] is False
        with factory() as session:
            task = session.get(AsyncTask, task_id)
            assert int(row["message_version"]) == 1 and task.message_version == 2
        provider_before_duplicates = deepcopy(provider)
        sender = RabbitSender(settings)
        try:
            sender(task_id, int(row["message_version"]), "text")
            sender(task_id, int(row["message_version"]) - 1, "text")
        finally:
            sender.app.close()
        deliveries = eventually(
            lambda: read_json(tmp_path / "generation-worker-report.json"),
            lambda value: (
                value is not None
                and len(value["deliveries"]) == 3
                and all(row["completed"] for row in value["deliveries"])
            ),
        )
        assert all(row["state"] == "SUCCESS" for row in deliveries["deliveries"])
        assert all(int(row["task_id"]) == task_id for row in deliveries["deliveries"])
        for row in deliveries["deliveries"]:
            assert row["delivery"]["exchange"] == namespace + ".tasks"
            assert row["delivery"]["routing_key"] == "text"
        assert read_json(tmp_path / "provider-report.json") == provider_before_duplicates
        assert all(worker.poll() is None for worker in processes.values())
    provider = read_json(tmp_path / "provider-report.json")
    assert provider["submissions"] == browser["task_post_count"] == 1
    assert provider["model"] == MODEL and provider["adapter"] == "openai_chat.v1"
    assert len(provider["requests"]) == len(provider["results"]) == 1
    wire = provider["requests"][0]
    assert wire["method"] == "POST" and wire["host"] == "api.agnes-ai.cn"
    assert wire["path"] == "/v1/chat/completions" and wire["http_status"] == 200
    assert wire["options"]["model"] == MODEL and wire["options"]["stream"] is True
    assert "messages" in wire["fields"] and wire["image_references"] == []
    assert provider["downloads"] == []
    assert not list(tmp_path.glob("supplier-inline-*.bin"))
    assert browser["width"] == width and browser["variant"] == variant
    assert browser["protocol"] == PROTOCOL and browser["terminal_status"] == "succeeded"
    assert browser["resource_post_count"] == 0
    assert browser["page_errors"] == browser["failed_routes"] == database_errors == []
    for field in (
        "original_connection_created_text",
        "skill_prompt_compiled",
        "pending_source_saved_before_close",
        "closed_window_before_provider_post",
        "private_template_changed_after_admission",
        "same_task_cookie_only_running_recovery",
        "original_consumer_bound_text",
        "real_delta_replay_matches_supplier_and_canonical_terminal",
        "final_cookie_only_recovery",
        "foreign_owner_denied",
    ):
        assert browser[field], field
    raw_text = provider["results"][0]["text"]
    actual_text = browser["canonical_text"]
    assert browser["raw_supplier_text"] == raw_text
    assert browser["text"] == actual_text == raw_text.strip()
    assert provider["results"][0]["status"] == "succeeded"
    assert all(marker in actual_text for marker in ("清晨车站", "红色礼盒"))
    assert 30 <= len(actual_text.strip()) <= 80
    assert "深夜机场" not in actual_text and "蓝色行李箱" not in actual_text
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == 2 and len(final["connections"]) == 1
    skill_node = next(item for item in final["nodes"] if item["id"] == "skill-input")
    text = next(item for item in final["nodes"] if item["id"] == browser["text_node_id"])
    assert skill_node["metadata"] == updated_private
    assert text["type"] == "text" and text["metadata"]["content"] == actual_text
    assert text["metadata"]["taskId"] == str(task_id)
    assert foreign.get(path + "/my-document").status_code == 404
    shared = owner.get(path).json()["source_document"]
    assert next(item for item in shared["nodes"] if item["id"] == "skill-input")["metadata"] == {}
    assert next(item for item in shared["nodes"] if item["id"] == text["id"])["metadata"] == {
        "content": actual_text,
        "fontSize": 14,
        "generatedFromNodeId": text["id"],
    }
    with factory() as session:
        tasks = list(session.scalars(select(AsyncTask)))
        assert len(tasks) == 1 and tasks[0].id == task_id and tasks[0].status == "succeeded"
        assert tasks[0].initiated_by == int(owner_user["id"]) and tasks[0].message_version == 2
        binding = session.scalar(select(CanvasTaskBinding))
        assert binding.node_key == binding.source_node_key == text["id"]
        assert binding.initiated_by == int(owner_user["id"])
        record = session.scalar(select(AIGenerationRecord))
        assert record.task_id == task_id and record.call_no == 1 and record.status == "succeeded"
        assert record.adapter == "openai_chat.v1" and record.text_content == raw_text
        assert record.config_snapshot["model_key"] == MODEL
        assert record.credential_cipher and secret not in record.credential_cipher
        frozen = record.request_data
        assert frozen["input"]["messages"] == [{"role": "user", "content": EXPECTED_PROMPT}]
        assert frozen["canvas_request"]["prompt"] == EXPECTED_PROMPT
        assert frozen["canvas_request"]["input"]["prompt"] == EXPECTED_PROMPT
        assert frozen["canvas_request"]["input"]["metadata"]["sourceNodeId"] == text["id"]
        assert frozen["source"]["source_node_key"] == text["id"]
        assert frozen["source_snapshot"]["source_node"]["id"] == text["id"]
        assert frozen["source_snapshot"]["source_node"]["type"] == "text"
        assert "skillSnapshot" not in frozen["source_snapshot"]["source_node"]["metadata"]
        assert frozen["resolved_parameters"] == {
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        assert binding.source_snapshot["request"] == frozen
        assert binding.source_snapshot["source_node"]["id"] == text["id"]
        assert binding.source_snapshot["source_node"]["type"] == "text"
        result = session.scalar(select(CanvasResult))
        assert result.attachment_status == "attached" and result.attachment_receipt_id is not None
        assert result.content_json == {"content": actual_text} and result.media_id is None
        deltas = list(
            session.scalars(select(CanvasTaskTextDelta).order_by(CanvasTaskTextDelta.sequence))
        )
        assert deltas and [delta.sequence for delta in deltas] == list(range(1, len(deltas) + 1))
        assert "".join(delta.content for delta in deltas) == raw_text
        assert all(
            delta.task_binding_id == binding.id
            and delta.generation_record_id == record.id
            and delta.created_by == int(owner_user["id"])
            and delta.byte_count == len(delta.content.encode())
            for delta in deltas
        )
        assert record.response_data["canvas_text_draft"] == raw_text
        skill = session.scalar(select(CanvasNode).where(CanvasNode.node_key == "skill-input"))
        assert skill.content_json["metadata"] == {}
        states = list(
            session.scalars(
                select(CanvasNodeUserState).where(
                    CanvasNodeUserState.canvas_id == skill.canvas_id,
                    CanvasNodeUserState.node_key == skill.node_key,
                )
            )
        )
        assert len(states) == 1 and states[0].user_id == int(owner_user["id"])
        assert states[0].draft_json == updated_private
        assert session.scalar(select(MediaFile.id)) is None
        delta_summary = {
            "count": len(deltas),
            "sequences": [delta.sequence for delta in deltas],
            "utf8_bytes": sum(delta.byte_count for delta in deltas),
            "raw_text_sha256": hashlib.sha256(raw_text.encode()).hexdigest(),
            "canonical_text_sha256": hashlib.sha256(actual_text.encode()).hexdigest(),
        }
        for value in (record.config_snapshot, frozen, record.response_data, shared):
            assert_no_secret(value, secret)
    for client in (foreign, outsider):
        assert client.get(TASKS + "/" + str(task_id)).status_code == 404
        assert client.get(TASKS + "/" + str(task_id) + "/text-deltas").status_code == 404
        assert client.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
    assert outsider.get(path).status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404
    assert_no_secret(browser, secret)
    assert_no_secret(provider, secret)
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    assert len(stack["roles"]) == 8
    assert len({row["pid"] for row in stack["roles"].values()}) == 8
    assert all(stack["cleanup"].values())
    assert database_errors == []
    (tmp_path / "database-errors.json").write_text(
        json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "scope": "one original Skill snapshot, Chat text, 1440, formal isolated stack",
                "width": width,
                "variant": variant,
                "model": MODEL,
                "protocol": PROTOCOL,
                "browser": browser,
                "provider": provider,
                "stack": stack,
                "broker_deliveries": deliveries,
                "normal_action_count": len(normal_deliveries),
                "delta_archive": delta_summary,
                "text_contract": {
                    "raw_supplier_text": raw_text,
                    "canonical_shared_content": actual_text,
                    "canonicalization": "durable_output applies record.text_content.strip()",
                },
                "python_postchecks": "passed",
                "content_verdict": (
                    "requires inspection of actual supplier text and canvas rendering"
                ),
                "wire_prompt_observation": "not collected by the unchanged observer",
                "natural_midstream_browser_loading": "not claimed; hold is before provider POST",
                "skill_object_freeze": "compiled prompt frozen; source snapshot is the text node",
                "media_worker_business_paths": "not exercised",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
