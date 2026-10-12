"""显式授权候选：原文字分镜、正式八角色与真实 Agnes Chat 单场。"""

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
    CanvasToolRun,
    CanvasToolSubmission,
    MediaFile,
)
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas_storyboard import canvas_storyboard_result, storyboard_system_prompt
from short_drama.tasks.publisher import RabbitSender
from tests.integration.test_canvas_agnes_live import assert_no_secret, live_key
from tests.integration.test_canvas_browser import stop_browser
from tests.integration.test_canvas_generation_broker import eventually
from tests.integration.test_canvas_generation_runtime import TASKS, WORKSPACE
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import canvas
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.integration.test_project_assistant_agnes_broker_live import (
    ROLE_IDS,
    official_stack,
    read_json,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_AGNES_LIVE",
                "RUN_AGNES_STORYBOARD_BROKER_LIVE",
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
                "RUN_CANVAS_BROKER_INTEGRATION",
            )
        ),
        reason=(
            "Explicit Agnes storyboard, formal AMQP, isolated storage and browser opt-in required"
        ),
    ),
]
ROOT = Path(__file__).resolve().parents[3]
MODEL = "agnes-3.0-flash"
PROTOCOL = "chat-completion"
NODE_ID = "runtime-node"
PROMPT = "清晨车站，旅客握着红色礼盒等待列车；列车进站，他深吸一口气后迈步走向车门。"
STYLE = {"presetId": "cinematic", "title": "电影", "prompt": "低饱和"}
REQUIREMENTS = "输出可直接编辑并用于批量生成图片和视频的分镜表。"


@pytest.mark.parametrize("width", [1440, 390])
def test_original_storyboard_real_agnes_through_formal_generation_worker(
    resource_app, database_errors, tmp_path, monkeypatch, width
):
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    node = shutil.which("node")
    assert node, "Node.js is required for explicit Agnes storyboard browser verification"
    suffix = uuid4().hex[:12]
    owner, owner_user = account(resource_app, "agsb_o_" + suffix)
    member, member_user = account(resource_app, "agsb_m_" + suffix)
    outsider, outside_user = account(resource_app, "agsb_x_" + suffix)
    project, path, document = canvas(owner, "agnes-storyboard-" + uuid4().hex)
    join(resource_app, owner, member, project["id"], member_user["id"])
    document["nodes"] = [
        {
            "id": NODE_ID,
            "type": "script",
            "title": "分镜脚本",
            "position": {"x": 420.25, "y": -20.5},
            "width": 920,
            "height": 360,
            "metadata": {
                "storyboard": {"rows": [], "visibleColumns": ["camera"], "referenceNodeIds": []}
            },
        },
        {
            "id": "style",
            "type": "text",
            "title": "项目画风 · 电影",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {
                "workflowKind": "styleboard",
                "stylePresetId": "cinematic",
                "content": "低饱和",
            },
        },
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "agnes-storyboard-existing-source-" + suffix},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    document = owner.get(path + "/my-document").json()["source_document"]
    source_style = next(item for item in document["nodes"] if item["id"] == "style")
    secret = live_key()
    created = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "Agnes私人文字分镜正式验收",
            "service_type": "text",
            "provider": "Agnes",
            "model_key": MODEL,
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": secret,
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": PROTOCOL},
        },
    )
    if created.status_code != 201:
        pytest.fail("Saved real storyboard model failed: HTTP " + str(created.status_code))
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
    browser_port = int(os.environ.get("AGNES_STORYBOARD_BROKER_BROWSER_PORT", "4199"))
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
        "memberUsername": member_user["username"],
        "outsideUsername": outside_user["username"],
        "password": PASSWORD,
        "projectId": project["id"],
        "canvasId": project["primary_canvas_id"],
        "sourceKey": document["id"],
        "nodeId": NODE_ID,
        "prompt": PROMPT,
        "projectStyle": STYLE,
        "requirements": REQUIREMENTS,
        "model": MODEL,
        "modelId": model["id"],
        "protocol": PROTOCOL,
        "width": width,
        "naturalMidstream": os.environ.get("AGNES_STORYBOARD_NATURAL_MIDSTREAM") == "1",
        "browserPort": browser_port,
        "releasePath": str(tmp_path / "real-provider-submit-allowed"),
        "providerReportPath": str(tmp_path / "provider-report.json"),
        "outputDirectory": str(tmp_path / "browser"),
    }
    with official_stack(
        factory,
        settings,
        tmp_path,
        generation_observer_module="tests.integration.agnes_storyboard_broker_observer",
    ) as (api_url, stack, processes):
        config["apiUrl"] = api_url
        process = subprocess.Popen(
            [node, "scripts/verify-agnes-storyboard-python.mjs"],
            cwd=ROOT / "frontend" / "canvas",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        timed_out = False
        try:
            output, errors = process.communicate(json.dumps(config), timeout=360)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert_no_secret(output + errors, secret)
        (tmp_path / "browser-stdout.json").write_text(output, encoding="utf-8")
        (tmp_path / "browser-stderr.log").write_text(errors, encoding="utf-8")
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
        if timed_out or process.returncode or not browser:
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            known_task = browser.get("task_id")
            known_task = (
                known_task
                if isinstance(known_task, str) and re.fullmatch(r"[1-9][0-9]*", known_task)
                else None
            )
            observed = owner.get(
                TASKS + "/" + known_task if known_task else TASKS,
                params={} if known_task else {"projectId": document["id"]},
            )
            try:
                task_data = observed.json() if observed.status_code == 200 else None
            except ValueError:
                task_data = None
            task_rows = (
                task_data
                if isinstance(task_data, list)
                else [task_data]
                if isinstance(task_data, dict)
                else []
            )
            task_rows = [row for row in task_rows if isinstance(row, dict)]
            reconciliation = {
                "timed_out": timed_out,
                "process_returncode": process.returncode,
                "browser_report_source": browser_source,
                "task_lookup_http_status": observed.status_code,
                "task_id_from_browser": known_task,
                "run_id_from_browser": browser.get("run_id"),
                "task_states": [
                    {
                        key: row.get(key)
                        for key in ("id", "status", "errorCode", "canRetry", "canResume")
                    }
                    for row in task_rows
                ],
                "provider_submissions": provider.get("submissions") if provider else None,
                "provider_requests": len(provider.get("requests", [])) if provider else None,
                "new_submission_performed": False,
            }
            assert_no_secret(reconciliation, secret)
            (tmp_path / "reconciliation.json").write_text(
                json.dumps(reconciliation, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if provider is not None and provider["submissions"] == 0 and not provider["requests"]:
                pytest.fail(
                    "Formal storyboard failed before provider POST: " + errors + "\n" + output
                )
            if provider is None and task_rows == []:
                pytest.fail("Formal storyboard failed before admission: " + errors + "\n" + output)
            pytest.exit(
                "Formal storyboard requires original-identity reconciliation without another POST: "
                + json.dumps(reconciliation, ensure_ascii=False)
                + "\n"
                + errors
                + "\n"
                + output,
                1,
            )
        assert provider is not None and browser["step"] == "complete"
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
            sender(task_id, 1, "text")
            sender(task_id, 0, "text")
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
    assert provider["submissions"] == browser["run_posts"] == browser["execute_posts"] == 1
    assert browser["task_posts"] == browser["resource_posts"] == 0
    assert browser["prepare_posts"] == browser["approve_posts"] == 1
    assert browser["trace_errors"] == []
    assert len(browser["provider_prepost_checks"]) == 3
    assert all(
        item["submissions"] == item["request_count"] == item["result_count"] == 0
        for item in browser["provider_prepost_checks"]
    )
    assert provider["model"] == MODEL and provider["adapter"] == "openai_chat.v1"
    assert len(provider["requests"]) == len(provider["results"]) == 1
    wire = provider["requests"][0]
    assert wire["method"] == "POST" and wire["host"] == "api.agnes-ai.cn"
    assert wire["path"] == "/v1/chat/completions" and wire["http_status"] == 200
    assert wire["options"]["model"] == MODEL and wire["options"]["stream"] is True
    assert "messages" in wire["fields"] and wire["image_references"] == []
    assert provider["downloads"] == [] and not list(tmp_path.glob("supplier-inline-*.bin"))
    assert browser["width"] == width and browser["protocol"] == PROTOCOL
    assert browser["terminal_status"] == "succeeded"
    assert browser["page_errors"] == browser["http_errors"] == database_errors == []
    if width == 390:
        assert len(browser["narrow_editor_scroll"]) == 3
        for evidence in browser["narrow_editor_scroll"]:
            assert evidence["before"]["scroll_width"] > evidence["before"]["client_width"]
            assert evidence["after"]["scroll_left"] > evidence["before"]["scroll_left"]
    for field in (
        "original_composer_created_approved_run",
        "creation_lifecycle_checked",
        "source_saved_and_private_loading",
        "closed_before_provider_post",
        "running_cookie_only_recovery",
        "original_consumer_bound_storyboard",
        "real_delta_replay_matches_supplier_and_canonical_terminal",
        "stable_rows_restored",
        "member_shared_rows_private_task_hidden",
    ):
        assert browser[field], field
    raw = provider["results"][0]["text"]
    assert provider["results"][0]["status"] == "succeeded"
    assert raw == browser["raw_supplier_text"]
    wire_observation = provider["wire_observation"]
    request_bytes = (tmp_path / wire_observation["request_file"]).read_bytes()
    response_bytes = (tmp_path / wire_observation["response_file"]).read_bytes()
    assert hashlib.sha256(request_bytes).hexdigest() == wire_observation["request_sha256"]
    assert hashlib.sha256(response_bytes).hexdigest() == wire_observation["response_sha256"]
    assert len(response_bytes) == wire_observation["response_bytes"] == wire["response_bytes"]
    assert wire_observation["response_mime"].split(";", 1)[0].strip().lower() == "text/event-stream"
    request_body = json.loads(request_bytes)
    assert sorted(request_body) == wire["fields"]
    assert request_body["model"] == MODEL and request_body["stream"] is True
    response_text = response_bytes.decode("utf-8").replace("\r\n", "\n")
    chunks, finishes, done = [], [], False
    for block in response_text.split("\n\n"):
        data = "\n".join(
            line[5:].lstrip(" ") for line in block.split("\n") if line.startswith("data:")
        )
        if not data:
            continue
        assert not done, "Supplier data appeared after SSE [DONE]"
        if data == "[DONE]":
            done = True
            continue
        packet = json.loads(data)
        for choice in packet.get("choices", []):
            content = choice.get("delta", {}).get("content")
            if content:
                assert isinstance(content, str)
                chunks.append(content)
            if choice.get("finish_reason") is not None:
                finishes.append(choice["finish_reason"])
    assert done and finishes == ["stop"]
    assert "".join(chunks) == raw
    assert len(chunks) == wire_observation["sse_delta_event_count"]
    assert wire_observation["done"] and wire_observation["finish_reasons"] == finishes
    assert_no_secret(request_body, secret)
    assert_no_secret(response_text, secret)
    if config["naturalMidstream"]:
        natural = browser["natural_midstream"]
        assert natural["task_id"] == str(browser["task_id"])
        assert natural["status_at_first_observation"] == natural["status_before_close"] == "running"
        assert 0 < natural["sequence_at_first_observation"] <= natural["sequence_before_close"]
        assert natural["source_status"] == "loading" and natural["source_content_empty"]
        assert natural["source_rows"] == []
        assert natural["text_event_requests_before_close"] >= 1
        assert natural["provider_submissions_before_close"] == 1
        assert natural["provider_results_before_close"] == 0
        assert natural["provider_response_complete_before_close"] is False
        assert natural["cookie_only_fresh_page"]
        assert raw.startswith(natural["first_text"]) and len(natural["first_text"]) < len(raw)
        assert raw.startswith(natural["replay_text"])
    canonical = browser["canonical_text"]
    assert canonical == raw.strip()
    rows, title = browser["final_rows"], browser["title"]
    assert [item["id"] for item in rows] == [f"shot:{task_id}:1", f"shot:{task_id}:2"]
    assert all(item["durationSeconds"] == 5 and item["characters"] == [] for item in rows)
    final = owner.get(path + "/my-document").json()["source_document"]
    script = next(item for item in final["nodes"] if item["id"] == NODE_ID)
    assert len(final["nodes"]) == 2 and final["connections"] == []
    final_style = next(item for item in final["nodes"] if item["id"] == "style")
    assert final_style == browser["style_before_provider"]
    assert {key: value for key, value in final_style.items() if key in source_style} == source_style
    assert set(final_style) - set(source_style) == {"createdAt", "updatedAt"}
    assert script["type"] == "script" and script["title"] == title
    assert script["metadata"]["content"] == canonical
    assert script["metadata"]["storyboard"]["rows"] == rows
    assert script["metadata"]["composerContent"] == PROMPT
    assert script["metadata"]["storyboardShotCount"] == "2"
    assert script["metadata"]["storyboardShotDuration"] == "5"
    assert script["metadata"]["taskId"] == str(task_id)
    shared = member.get(path + "/my-document").json()["source_document"]
    shared_script = next(item for item in shared["nodes"] if item["id"] == NODE_ID)
    assert (
        shared_script["title"] == title and shared_script["metadata"]["storyboard"]["rows"] == rows
    )
    assert shared_script["metadata"]["content"] == canonical
    for key in (
        "taskId",
        "composerContent",
        "storyboardShotCount",
        "storyboardShotDuration",
        "prompt",
    ):
        assert key not in shared_script["metadata"]
    with factory() as session:
        tasks = list(session.scalars(select(AsyncTask)))
        assert len(tasks) == 1 and tasks[0].id == task_id and tasks[0].status == "succeeded"
        assert tasks[0].initiated_by == int(owner_user["id"]) and tasks[0].message_version == 2
        binding = session.scalar(select(CanvasTaskBinding))
        assert binding.node_key == binding.source_node_key == NODE_ID
        assert binding.initiated_by == int(owner_user["id"])
        record = session.scalar(select(AIGenerationRecord))
        assert record.task_id == task_id and record.call_no == 1 and record.status == "succeeded"
        assert record.adapter == "openai_chat.v1" and record.text_content == raw
        assert record.config_snapshot["model_key"] == MODEL
        assert record.credential_cipher and secret not in record.credential_cipher
        frozen = record.request_data
        assert_no_secret(frozen, secret)
        (tmp_path / "frozen-request.json").write_text(
            json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        request = frozen["canvas_request"]
        assert request["prompt"] == request["input"]["prompt"] == PROMPT
        assert request["operation"] == "storyboard"
        assert request["input"]["shotCount"] == 2 and request["input"]["shotDurationSeconds"] == 5
        assert request["input"]["characters"] == request["input"]["canvasAssets"] == []
        assert request["input"]["projectStyle"] == STYLE
        assert request["input"]["requirements"] == REQUIREMENTS
        normalized_request = CanvasRuntimeTaskCreate.model_validate(request).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        assert frozen["input"]["messages"] == [
            {"role": "system", "content": storyboard_system_prompt(normalized_request)},
            {"role": "user", "content": PROMPT},
        ]
        assert request_body["messages"] == frozen["input"]["messages"]
        assert frozen["resolved_parameters"] == {
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        assert binding.source_snapshot["request"] == frozen
        assert frozen["source"]["source_node_key"] == NODE_ID
        source = frozen["source_snapshot"]["source_node"]
        assert source["id"] == NODE_ID and source["type"] == "script"
        assert source["metadata"]["composerContent"] == PROMPT
        expected_snapshot = {
            "nodeId": NODE_ID,
            "rows": [],
            "projectStyle": STYLE,
            "characters": [],
            "canvasAssets": [],
        }
        assert frozen["canvas_storyboard_source"] == expected_snapshot
        structured = canvas_storyboard_result(raw, request, str(task_id))
        assert structured == {"title": title, "rows": rows}
        assert record.response_data["canvas_storyboard"] == structured
        result = session.scalar(select(CanvasResult))
        assert result.attachment_status == "attached" and result.attachment_receipt_id is not None
        assert result.media_id is None
        assert result.content_json == {
            "content": canonical,
            "storyboard": {"rows": rows},
            "storyboardTitle": title,
        }
        run = session.scalar(select(CanvasToolRun))
        submission = session.scalar(select(CanvasToolSubmission))
        assert str(run.id) == browser["run_id"] and run.user_id == int(owner_user["id"])
        assert run.state_json["kind"] == "storyboard" and run.source_media_id is None
        assert run.source_snapshot == expected_snapshot
        assert submission.run_id == run.id and submission.user_id == int(owner_user["id"])
        assert str(submission.id) == browser["submission_id"]
        assert submission.item_key == "storyboard" and submission.approved_at
        assert submission.async_task_id == task_id and submission.request_json == request
        assert frozen["canvas_tool_submission_id"] == str(submission.id)
        assert frozen["canvas_tool_run_id"] == str(run.id)
        deltas = list(
            session.scalars(select(CanvasTaskTextDelta).order_by(CanvasTaskTextDelta.sequence))
        )
        assert deltas and [delta.sequence for delta in deltas] == list(range(1, len(deltas) + 1))
        assert "".join(delta.content for delta in deltas) == raw
        assert all(
            delta.task_binding_id == binding.id
            and delta.generation_record_id == record.id
            and delta.created_by == int(owner_user["id"])
            and delta.byte_count == len(delta.content.encode())
            for delta in deltas
        )
        if config["naturalMidstream"]:
            assert (
                "".join(
                    delta.content
                    for delta in deltas
                    if delta.sequence <= browser["natural_midstream"]["replay_sequence"]
                )
                == browser["natural_midstream"]["replay_text"]
            )
        assert record.response_data["canvas_text_draft"] == raw
        assert session.scalar(select(MediaFile.id)) is None
        delta_summary = {
            "count": len(deltas),
            "sequences": [delta.sequence for delta in deltas],
            "utf8_bytes": sum(delta.byte_count for delta in deltas),
            "raw_text_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "canonical_text_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        }
        for value in (record.config_snapshot, frozen, record.response_data, shared):
            assert_no_secret(value, secret)
    for client in (member, outsider):
        assert client.get(TASKS + "/" + str(task_id)).status_code == 404
        assert client.get(TASKS + "/" + str(task_id) + "/text-deltas").status_code == 404
        assert (
            client.get("/api/v1/canvas-runtime/creation-runs/" + browser["run_id"]).status_code
            == 404
        )
        assert client.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
    assert outsider.get(path).status_code == outsider.get(path + "/my-document").status_code == 404
    assert_no_secret(browser, secret)
    assert_no_secret(provider, secret)
    for log in tmp_path.glob("*.log"):
        assert_no_secret(log.read_text(encoding="utf-8"), secret)
    assert len(stack["roles"]) == len({row["pid"] for row in stack["roles"].values()}) == 8
    assert all(stack["cleanup"].values()) and database_errors == []
    (tmp_path / "database-errors.json").write_text(
        json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "scope": (
                    f"original storyboard, {width}, two five-second shots, zero defined characters"
                ),
                "width": width,
                "model": MODEL,
                "protocol": PROTOCOL,
                "browser": browser,
                "provider": provider,
                "stack": stack,
                "broker_deliveries": deliveries,
                "normal_action_count": len(normal_deliveries),
                "delta_archive": delta_summary,
                "python_postchecks": "passed",
                "content_verdict": (
                    "requires actual two-shot content and original canvas inspection"
                ),
                "wire_messages_mime_raw_sse": {
                    "scope": "logical request body and decoded SSE bytes; no packet capture",
                    "status": "passed",
                    **wire_observation,
                },
                "natural_midstream_loading": (
                    "passed; real running delta and cookie-only interruption recovery"
                    if config["naturalMidstream"]
                    else "not claimed; hold is before provider POST"
                ),
                "character_support": "zero-character case only; project role binding not exercised",
                "media_worker_business_paths": "not exercised",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
