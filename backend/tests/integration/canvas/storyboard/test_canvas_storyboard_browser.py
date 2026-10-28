"""已有分镜节点的原编辑器、稳定行身份与窗口恢复；不开放源禁用入口。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import AsyncTask, CanvasResult, CanvasTaskBinding, MediaFile
from short_drama.domain.canvas.document.canvas import CanvasEdge, CanvasNode
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import canvas
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable existing storyboard UI with isolated HTTP/MySQL/MinIO",
    ),
]


@pytest.mark.parametrize("width", [1440, 600])
def test_existing_storyboard_original_editor_preserves_rows_and_private_settings(
    resource_app, database_errors, tmp_path, width
):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit existing storyboard browser verification"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    owner, _ = account(resource_app, "storyboard_owner")
    member, member_user = account(resource_app, "storyboard_member")
    outsider, _ = account(resource_app, "storyboard_outside")
    project, path, document = canvas(owner, "existing-storyboard-project")
    document["nodes"] = [
        {
            "id": "existing-script",
            "type": "script",
            "title": "分镜脚本",
            "position": {"x": -460, "y": -180},
            "width": 920,
            "height": 360,
            "metadata": {
                "workflowKind": "script",
                "status": "idle",
                "storyboard": {
                    "rows": [],
                    "visibleColumns": [
                        "shotNumber",
                        "durationSeconds",
                        "videoMotionPrompt",
                        "dialogue",
                        "assets",
                    ],
                    "referenceNodeIds": [],
                },
            },
        }
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "existing-storyboard-fixture"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "storyboard_owner",
            "memberUsername": "storyboard_member",
            "password": PASSWORD,
            "width": width,
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-storyboard-python.mjs"],
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
            pytest.fail(f"Storyboard browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Storyboard browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert report["step"] == "complete" and report["width"] == width
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["task_posts"] == report["resource_posts"] == report["creation_run_posts"] == 0
    for flag in (
        "creation_menu_disabled_like_source",
        "empty_state_and_first_row",
        "last_inline_row_delete_disabled",
        "inline_duration_bounds",
        "inline_edit_and_delete",
        "missing_style_blocks_keyboard_generation",
        "original_fullscreen_editor",
        "column_visibility_saved",
        "duplicate_and_reorder_keep_ids",
        "search_filters_rows",
        "direct_and_keyframe_settings",
        "original_media_nodes_and_row_connections",
        "media_creation_not_repeated",
        "cookie_only_recovery",
    ):
        assert report[flag], flag
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == 7 and len(final["connections"]) == 6
    script = next(item for item in final["nodes"] if item["id"] == "existing-script")
    assert (script["id"], script["type"], script["title"], script["width"]) == (
        "existing-script",
        "script",
        "分镜脚本",
        920,
    )
    metadata = script["metadata"]
    assert metadata["storyboard"]["rows"] == report["final_rows"]
    assert [row["shotNumber"] for row in metadata["storyboard"]["rows"]] == [1, 2, 3]
    assert len({row["id"] for row in metadata["storyboard"]["rows"]}) == 3
    assert metadata["storyboard"]["visibleColumns"] == report["final_columns"]
    assert metadata["storyboard"]["referenceNodeIds"] == []
    assert metadata["composerContent"] == report["private_prompt"]
    assert metadata["storyboardShotCount"] == "3"
    assert metadata["storyboardShotDuration"] == "10"
    assert metadata["storyboardVideoInputMode"] == "keyframe"
    assert member.get(path + "/my-document").status_code == 404
    shared = owner.get(path).json()["source_document"]
    shared_nodes = {item["id"]: item for item in shared["nodes"]}
    shared_metadata = shared_nodes[script["id"]]["metadata"]
    assert shared_metadata["storyboard"] == metadata["storyboard"]
    assert shared["connections"] == final["connections"]
    for key in (
        "composerContent",
        "storyboardShotCount",
        "storyboardShotDuration",
        "storyboardVideoInputMode",
    ):
        assert key not in shared_metadata
    for row in metadata["storyboard"]["rows"]:
        for kind, field, prompt_field in (
            ("image", "imageNodeId", "plotDescription"),
            ("video", "videoNodeId", "videoMotionPrompt"),
        ):
            target = next(item for item in final["nodes"] if item["id"] == row[field])
            assert target["type"] == kind
            assert target["metadata"]["workflowKind"] == "shot"
            assert target["metadata"]["shotIndex"] == row["shotNumber"]
            assert target["metadata"]["prompt"] == row[prompt_field]
            assert target["metadata"]["composerContent"] == row[prompt_field]
            if kind == "video":
                assert target["metadata"]["seconds"] == str(row["durationSeconds"])
            edges = [edge for edge in final["connections"] if edge["toNodeId"] == row[field]]
            assert len(edges) == 1
            assert edges[0]["fromNodeId"] == script["id"]
            assert edges[0]["fromHandleId"] == "row:" + row["id"]
            assert edges[0]["relation"] == "storyboard-output"
            assert edges[0]["storyboardRowId"] == row["id"]
            for key in ("prompt", "composerContent", "model", "seconds"):
                assert key not in shared_nodes[row[field]]["metadata"]
    assert outsider.get(path + "/my-document").status_code == 404
    with resource_app[1]() as session:
        stored = session.scalar(select(CanvasNode).where(CanvasNode.node_key == "existing-script"))
        assert stored.kind == "script"
        assert stored.content_json["metadata"]["storyboard"] == metadata["storyboard"]
        assert len(list(session.scalars(select(CanvasNode.id)))) == 7
        edges = list(session.scalars(select(CanvasEdge)))
        assert len(edges) == 6
        for edge in edges:
            assert edge.from_node_key == script["id"]
            assert edge.relation == "storyboard-output"
            assert edge.from_port == "row:" + edge.context_json["storyboardRowId"]
        assert session.scalar(select(AsyncTask.id)) is None
        assert session.scalar(select(CanvasResult.id)) is None
        assert session.scalar(select(CanvasTaskBinding.id)) is None
        assert session.scalar(select(MediaFile.id)) is None
    assert database_errors == []
