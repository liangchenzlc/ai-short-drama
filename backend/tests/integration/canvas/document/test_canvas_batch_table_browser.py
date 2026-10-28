"""原批量表菜单、参考上传、串行调度和仅 cookies 恢复；供应商为受控 HTTP。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskBinding, MediaFile
from short_drama.domain.canvas.document.canvas import CanvasNode
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import (
    broker_app as broker_app,
)
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    WORKSPACE,
    records,
    seed,
)
from tests.integration.canvas.image.test_canvas_image_tools_browser import minio_bytes
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_library_deletion import commit
from tests.integration.canvas.resources.test_canvas_resources import ROOT, png
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original batch table with isolated AMQP/HTTP/MySQL/MinIO",
    ),
]
ROW_PROMPTS = ["批量行一：保留人物身份，替换参考服装。", "批量行二：保持背景和光线，替换参考服装。"]
GLOBAL_PROMPT = "统一批量提示词：保留两张参考图的细节，背景使用冷灰色。"


@pytest.mark.parametrize("scenario,width", [("row_prompts", 1440), ("global_prompt", 600)])
def test_original_batch_table_recovers_waiting_row_without_duplicate_submission(
    broker_app, database_errors, tmp_path, scenario, width
):
    runtime = broker_app
    node = shutil.which("node")
    assert node, "Node.js is required for explicit original batch table verification"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    owner, owner_user, project, path, request = seed(runtime.app, "batch_table_owner", kind="image")
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    model = owner.get(model_path).json()
    patched = owner.patch(
        model_path,
        json={
            "row_version": model["row_version"],
            "base_url": runtime.url,
            "apikey": "canvas-batch-table-placeholder-not-a-real-key",
        },
    )
    assert patched.status_code == 200, patched.text
    workspace = owner.get(WORKSPACE).json()
    preferences = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {**workspace["preferences"], "count": "1"},
        },
    )
    assert preferences.status_code == 200, preferences.text
    initial = owner.get(path + "/my-document").json()["source_document"]
    initial["nodes"] = []
    initial["connections"] = []
    commit(owner, path, initial, "batch-table-empty")
    outsider, _ = account(runtime.app, "batch_table_outsider")
    originals = [png("red"), png("blue")]
    runtime.hold_images.set()
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial["id"],
            "username": "batch_table_owner",
            "password": PASSWORD,
            "images": [base64.b64encode(value).decode() for value in originals],
            "rowPrompts": ROW_PROMPTS,
            "globalPrompt": GLOBAL_PROMPT if scenario == "global_prompt" else "",
            "scenario": scenario,
            "width": width,
            "imageReleaseFile": str(runtime.image_release_path),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-batch-table-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=240)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Batch table browser timed out: {errors}\n{output}")
        finally:
            runtime.images_released.set()
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Batch table browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["scenario"] == scenario
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["uploads"] == len(report["submissions"]) == 2
    for flag in (
        "original_menu_created",
        "defaults_and_column_limits",
        "original_filechoosers_used",
        "inherited_references_and_operation_prompt",
        "disabled_row_excluded",
        "cancelled_confirmation_kept_graph",
        "idle_cookie_only_recovery",
        "running_and_waiting_cookie_only_recovery",
        "waiting_row_scheduled_after_recovery",
        "completed_header_disabled",
        "final_cookie_only_recovery",
    ):
        assert report[flag], flag
    assert records(runtime.app, owner_user) == {"tasks": 2, "records": 2, "bindings": 2}
    assert runtime.image_started.is_set() and len(runtime.posts) == 2
    prompts = [GLOBAL_PROMPT, GLOBAL_PROMPT] if scenario == "global_prompt" else ROW_PROMPTS
    for index, (target_path, parts) in enumerate(runtime.posts):
        assert target_path == "/v1/images/edits"
        assert [(mime, data) for name, filename, mime, data in parts if filename] == [
            ("image/png", original) for original in originals
        ]
        assert (
            next(data for name, filename, mime, data in parts if name == "prompt").decode()
            == (prompts[index])
        )
        assert next(data for name, filename, mime, data in parts if name == "n") == b"1"
    final = owner.get(path + "/my-document").json()["source_document"]
    assert final["revision"].isdecimal()
    assert len(final["nodes"]) == 5 and len(final["connections"]) == 6
    table_node = next(item for item in final["nodes"] if item["id"] == report["table_node_id"])
    assert (table_node["type"], table_node["width"], table_node["height"]) == (
        "batch-table",
        1280,
        560,
    )
    table = table_node["metadata"]["batchTable"]
    assert table["operation"] == "try_on" and table["concurrency"] == 1
    assert table.get("globalPrompt", "") == config["globalPrompt"]
    assert len(table["referenceColumns"]) == len(table["rows"]) == 2
    assert [row["prompt"] for row in table["rows"]] == ROW_PROMPTS
    source_nodes = report["source_nodes"]
    source_ids = [source["id"] for source in source_nodes]
    source_media_ids = [
        source["metadata"]["storageKey"].removeprefix("resource:") for source in source_nodes
    ]
    for source in source_nodes:
        assert next(item for item in final["nodes"] if item["id"] == source["id"]) == source
    batch = table_node["metadata"]["generationBatches"][-1]
    assert batch["id"] == report["batch_id"] and batch["status"] == "completed"
    assert batch["concurrency"] == 1 and batch["mode"] == "batch_image"
    assert [item["status"] for item in batch["items"]] == ["succeeded", "succeeded"]
    public = owner.get(path).json()["source_document"]
    public_table = next(item for item in public["nodes"] if item["id"] == table_node["id"])
    assert "batchTable" not in public_table["metadata"]
    assert "generationBatches" not in public_table["metadata"]
    assert outsider.get(path + "/my-document").status_code == 404
    media_to_check = list(zip(source_media_ids, originals, strict=True))
    with runtime.app[1]() as session:
        stored_table = session.scalar(
            select(CanvasNode).where(CanvasNode.node_key == table_node["id"])
        )
        assert stored_table.kind == "batch-table"
        assert "batchTable" not in stored_table.content_json["metadata"]
        assert "generationBatches" not in stored_table.content_json["metadata"]
        for index, row in enumerate(table["rows"]):
            assert row["enabled"] and row["inputNodeIds"] == source_ids
            assert row["outputNodeId"] == report["output_nodes"][index]["id"]
            result = next(item for item in final["nodes"] if item["id"] == row["outputNodeId"])
            assert result["metadata"]["status"] == "success"
            assert result["metadata"]["prompt"] == prompts[index]
            task_id = report["output_nodes"][index]["task_id"]
            assert task_id.isdecimal() and task_id == batch["items"][index]["taskId"]
            task = session.get(AsyncTask, int(task_id))
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
            )
            binding = session.scalar(
                select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
            )
            assert task.status == record.status == "succeeded"
            assert binding.node_key == binding.source_node_key == result["id"]
            assert record.request_data["input"]["reference_media_ids"] == source_media_ids
            assert record.request_data["input"]["prompt"] == prompts[index]
            key = result["metadata"]["storageKey"]
            assert key == report["output_nodes"][index]["storage_key"] and key.startswith(
                "resource:"
            )
            media_id = key.removeprefix("resource:")
            assert (
                report["output_nodes"][index]["sha256"] == hashlib.sha256(runtime.image).hexdigest()
            )
            stored = session.get(MediaFile, int(media_id))
            assert (
                stored.published_at is not None
                and minio_bytes(runtime.app, stored) == runtime.image
            )
            media_to_check.append((media_id, runtime.image))
            edges = [edge for edge in final["connections"] if edge["toNodeId"] == result["id"]]
            assert len(edges) == 3
            assert [
                edge["fromNodeId"] for edge in edges if edge.get("relation") != "batch-output"
            ] == source_ids
            table_edge = next(edge for edge in edges if edge.get("relation") == "batch-output")
            assert (
                table_edge["fromNodeId"] == table_node["id"]
                and table_edge["storyboardRowId"] == row["id"]
            )
            public_result = next(item for item in public["nodes"] if item["id"] == result["id"])
            assert public_result["metadata"]["storageKey"] == key
            for private in ("taskId", "model", "prompt", "composerContent", "batchInputNodeIds"):
                assert private not in public_result["metadata"]
            assert outsider.get(f"{TASKS}/{task_id}").status_code == 404
        for media_id, expected in media_to_check:
            assert minio_bytes(runtime.app, session.get(MediaFile, int(media_id))) == expected
    for media_id, expected in media_to_check:
        assert owner.get(f"{ROOT}/{media_id}/file").content == expected
        assert outsider.get(f"{ROOT}/{media_id}/file").status_code == 404
    assert database_errors == []
