"""已有分镜的图片/动作板批次经真实 AMQP 恢复；供应商为受控 HTTP。"""

import hashlib
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskBinding, MediaFile
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
from tests.integration.canvas.resources.test_canvas_resources import ROOT, png, upload
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
        reason="Enable isolated storyboard media AMQP/HTTP/MySQL/MinIO/browser verification",
    ),
]
PLOTS = ["人物走进车站。", "人物停在检票口。", "列车驶离站台。"]
IMAGE_PROMPTS = [
    "已完成的分镜图。",
    "中景，人物停在检票口，冷灰色光线。",
    "远景，列车驶离站台，保持场景连续。",
]


@pytest.mark.parametrize("mode", ["storyboard_image", "action_board"])
@pytest.mark.parametrize("width", [1440, 600])
def test_original_storyboard_media_batch_restores_exact_tasks_and_shared_works(
    broker_app, database_errors, tmp_path, mode, width
):
    runtime = broker_app
    node = shutil.which("node")
    assert node, "Node.js is required for explicit storyboard media browser verification"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    owner, user, project, path, request = seed(runtime.app, "storyboard_media_owner", kind="image")
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    model = owner.get(model_path).json()
    changed = owner.patch(
        model_path,
        json={
            "row_version": model["row_version"],
            "base_url": runtime.url,
            "apikey": "storyboard-media-fixture-not-a-real-key",
        },
    )
    assert changed.status_code == 200, changed.text
    workspace = owner.get(WORKSPACE).json()
    saved = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {**workspace["preferences"], "count": "1"},
        },
    )
    assert saved.status_code == 200, saved.text
    document = owner.get(path + "/my-document").json()["source_document"]
    original = png("red")
    uploaded = upload(owner, original, source=document["id"], key="storyboard-existing-image")
    assert uploaded.status_code == 200, uploaded.text
    resource = uploaded.json()["resource"]
    existing = {
        "id": "completed-image",
        "type": "image",
        "title": "镜头 1 · 已完成作品",
        "position": {"x": 1060, "y": 0},
        "width": 320,
        "height": 240,
        "metadata": {
            "content": f"{ROOT}/{resource['id']}/file",
            "storageKey": "resource:" + resource["id"],
            "status": "success",
            "workflowKind": "shot" if mode == "storyboard_image" else "action_board",
            "shotIndex": 1,
            "prompt": "已完成作品的私人提示词",
        },
    }
    rows = [
        {
            "id": f"existing-shot-{index}",
            "shotNumber": index,
            "durationSeconds": 5,
            "plotDescription": plot,
            "imageGenerationPrompt": IMAGE_PROMPTS[index - 1],
            "videoMotionPrompt": "镜头缓慢移动，" + plot,
            "dialogue": "",
            "camera": "中景，平视",
            "characters": [],
            "assetBindings": [],
            "status": "idle",
            **(
                {"imageNodeId": existing["id"]} if index == 1 and mode == "storyboard_image" else {}
            ),
        }
        for index, plot in enumerate(PLOTS, 1)
    ]
    script = {
        "id": "existing-script",
        "type": "script",
        "title": "分镜媒体批次",
        "position": {"x": 0, "y": 0},
        "width": 920,
        "height": 420,
        "metadata": {
            "workflowKind": "script",
            "storyboard": {
                "rows": rows,
                "visibleColumns": [
                    "shotNumber",
                    "durationSeconds",
                    "videoMotionPrompt",
                    "dialogue",
                    "assets",
                ],
                "referenceNodeIds": [existing["id"]] if mode == "storyboard_image" else [],
            },
        },
    }
    document["nodes"] = [script, existing]
    document["connections"] = []
    commit(owner, path, document, "storyboard-media-fixture")
    initial = owner.get(path + "/my-document").json()["source_document"]
    existing = deepcopy(next(item for item in initial["nodes"] if item["id"] == existing["id"]))
    member, member_user = account(runtime.app, "storyboard_media_member")
    outsider, _ = account(runtime.app, "storyboard_media_outsider")
    runtime.hold_images.set()
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "storyboard_media_owner",
            "memberUsername": "storyboard_media_member",
            "password": PASSWORD,
            "width": width,
            "mode": mode,
            "releaseFile": str(runtime.image_release_path),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-storyboard-media-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=300)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Storyboard media browser timed out: {errors}\n{output}")
        finally:
            runtime.images_released.set()
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Storyboard media browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and (report["mode"], report["width"]) == (mode, width)
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert len(report["submissions"]) == 2 and report["resource_posts"] == 0
    for flag in (
        "original_menu_and_cancel",
        "completed_row_excluded",
        "active_menu_disabled",
        "running_cookie_only_recovery",
        "same_tasks_and_batch",
        "completed_menu_disabled",
        "final_cookie_only_recovery",
    ):
        assert report[flag], flag
    assert records(runtime.app, user) == {"tasks": 2, "records": 2, "bindings": 2}
    assert runtime.image_started.is_set() and len(runtime.posts) == 2
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == 4
    final_existing = next(item for item in final["nodes"] if item["id"] == existing["id"])
    assert final_existing == report["original_image"]
    for key in ("storageKey", "content", "shotIndex", "workflowKind", "prompt"):
        assert final_existing["metadata"][key] == existing["metadata"][key]
    final_script = next(item for item in final["nodes"] if item["id"] == script["id"])
    final_rows = final_script["metadata"]["storyboard"]["rows"]
    assert [row["id"] for row in final_rows] == [row["id"] for row in rows]
    batch = final_script["metadata"]["generationBatches"][-1]
    assert batch["id"] == report["batch_id"] and batch["mode"] == mode
    assert batch["status"] == "completed" and len(batch["items"]) == 2
    assert [item["rowId"] for item in batch["items"]] == [row["id"] for row in rows[1:]]
    assert [item["status"] for item in batch["items"]] == ["succeeded", "succeeded"]
    assert member.get(path + "/my-document").status_code == 404
    shared = owner.get(path).json()["source_document"]
    shared_script = next(item for item in shared["nodes"] if item["id"] == script["id"])
    assert "generationBatches" not in shared_script["metadata"]
    assert outsider.get(path + "/my-document").status_code == 404
    with runtime.app[1]() as session:
        for index, item in enumerate(batch["items"], 1):
            target = next(node for node in final["nodes"] if node["id"] == item["nodeId"])
            assert target["metadata"]["shotIndex"] == rows[index]["shotNumber"]
            if mode == "storyboard_image":
                assert final_rows[index]["imageNodeId"] == target["id"]
                assert target["metadata"]["workflowKind"] == "shot"
            else:
                assert target["metadata"]["workflowKind"] == "action_board"
                assert (
                    target["metadata"]["actionBoardRows"],
                    target["metadata"]["actionBoardColumns"],
                ) == (4, 3)
            task_id = item["taskId"]
            assert task_id.isdecimal() and task_id == target["metadata"]["taskId"]
            task = session.get(AsyncTask, int(task_id))
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
            )
            binding = session.scalar(
                select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
            )
            assert task.status == record.status == "succeeded" and task.message_status == "idle"
            assert binding.node_key == binding.source_node_key == target["id"]
            expected_prompt = target["metadata"]["prompt"]
            if mode == "storyboard_image":
                expected_prompt = "参考资产：@图片1\n" + IMAGE_PROMPTS[index]
                assert target["metadata"]["prompt"] in (IMAGE_PROMPTS[index], expected_prompt)
                assert target["metadata"]["prompt"] == report["running_prompts"][target["id"]]
            assert record.request_data["input"]["prompt"] == expected_prompt
            expected_refs = [resource["id"]] if mode == "storyboard_image" else []
            assert record.request_data["input"]["reference_media_ids"] == expected_refs
            matching_posts = [
                (provider_path, body)
                for provider_path, body in runtime.posts
                if (
                    next(data for name, filename, mime, data in body if name == "prompt").decode()
                    if isinstance(body, list)
                    else body["prompt"]
                )
                == record.request_data["input"]["prompt"]
            ]
            assert len(matching_posts) == 1
            provider_path, body = matching_posts[0]
            if mode == "storyboard_image":
                assert provider_path == "/v1/images/edits"
                assert [(mime, data) for name, filename, mime, data in body if filename] == [
                    ("image/png", original)
                ]
                provider_prompt = next(
                    data for name, filename, mime, data in body if name == "prompt"
                ).decode()
            else:
                assert provider_path == "/v1/images/generations"
                provider_prompt = body["prompt"]
                assert (
                    "严格 3 列 4 行" in provider_prompt
                    and rows[index]["plotDescription"] in provider_prompt
                )
            assert provider_prompt == expected_prompt
            edges = [edge for edge in final["connections"] if edge["toNodeId"] == target["id"]]
            shot_edge = next(edge for edge in edges if edge["fromNodeId"] == script["id"])
            assert shot_edge["fromHandleId"] == "row:" + rows[index]["id"]
            if mode == "storyboard_image":
                assert shot_edge["relation"] == "storyboard-output"
                assert shot_edge["storyboardRowId"] == rows[index]["id"]
                assert len(edges) == 2 and any(
                    edge["fromNodeId"] == existing["id"] for edge in edges
                )
            key = target["metadata"]["storageKey"]
            media_id = key.removeprefix("resource:")
            media = session.get(MediaFile, int(media_id))
            assert (
                media.published_at is not None and minio_bytes(runtime.app, media) == runtime.image
            )
            output_node = next(
                node for node in report["output_nodes"] if node["id"] == target["id"]
            )
            assert output_node["sha256"] == hashlib.sha256(runtime.image).hexdigest()
            public = next(node for node in shared["nodes"] if node["id"] == target["id"])
            assert public["metadata"]["storageKey"] == key
            for private in ("prompt", "composerContent", "model", "taskId"):
                assert private not in public["metadata"]
            assert member.get(f"{TASKS}/{task_id}").status_code == 404
            assert owner.get(f"{ROOT}/{media_id}/file").content == runtime.image
            assert member.get(f"{ROOT}/{media_id}/file").status_code == 404
            assert outsider.get(f"{ROOT}/{media_id}/file").status_code == 404
        assert minio_bytes(runtime.app, session.get(MediaFile, int(resource["id"]))) == original
    assert database_errors == []
