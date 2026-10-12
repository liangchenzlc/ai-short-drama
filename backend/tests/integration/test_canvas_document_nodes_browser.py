"""已有文档/对比节点的原连线、渲染与窗口恢复；源创建入口保持隐藏。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import AsyncTask, CanvasResult, CanvasTaskBinding, MediaFile
from short_drama.domain.canvas import CanvasNode
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_image_tools_browser import minio_bytes
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import ROOT, canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable existing document node UI with isolated HTTP/MySQL/MinIO",
    ),
]
SPECS = {
    "markdown": ("Markdown", 420, 320),
    "svg": ("SVG", 420, 320),
    "html": ("HTML", 520, 380),
    "chart": ("图表", 480, 320),
    "compare": ("对比", 520, 320),
}


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize("kind", list(SPECS))
def test_existing_document_node_uses_original_inputs_and_survives_cookie_only_recovery(
    resource_app, database_errors, tmp_path, kind, width
):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit existing document browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    owner, _ = account(resource_app, "document_node_owner")
    member, member_user = account(resource_app, "document_node_member")
    outsider, _ = account(resource_app, "document_node_outside")
    project, path, document = canvas(owner, "document-node-project")
    join(resource_app, owner, member, project["id"], member_user["id"])
    title, node_width, node_height = SPECS[kind]
    document["nodes"] = [
        {
            "id": "existing-document",
            "type": kind,
            "title": title,
            "position": {"x": -node_width / 2, "y": -node_height / 2},
            "width": node_width,
            "height": node_height,
            "metadata": {"status": "idle", **({} if kind == "compare" else {"content": ""})},
        }
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "existing-document-fixture"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    media = []
    originals = [png("red"), png("blue")] if kind == "compare" else []
    for index, original in enumerate(originals):
        uploaded = upload(
            owner,
            original,
            source=document["id"],
            key=f"document-compare-source-{index}",
        )
        assert uploaded.status_code == 200, uploaded.text
        media.append(uploaded.json()["resource"])
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "document_node_owner",
            "memberUsername": "document_node_member",
            "password": PASSWORD,
            "kind": kind,
            "width": width,
            "title": title,
            "media": media,
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-document-nodes-python.mjs"],
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
            pytest.fail(f"Document node browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Document node browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert report["step"] == "complete" and report["kind"] == kind and report["width"] == width
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["task_posts"] == report["resource_posts"] == 0
    for flag in (
        "creation_menu_absent_like_source",
        "empty_state_seen",
        "original_inputs_linked",
        "original_viewer_rendered",
        "cookie_only_recovery",
        "member_view_restored",
    ):
        assert report[flag], flag
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == (3 if kind == "compare" else 2)
    assert len(final["connections"]) == (2 if kind == "compare" else 1)
    viewer = next(item for item in final["nodes"] if item["id"] == "existing-document")
    assert (viewer["type"], viewer["title"], viewer["width"], viewer["height"]) == (
        kind,
        title,
        node_width,
        node_height,
    )
    shared = member.get(path + "/my-document").json()["source_document"]
    if kind == "compare":
        assert report["single_image_empty_state"] and report["split_is_transient"]
        assert viewer["metadata"].get("content", "") == ""
        for index, resource in enumerate(media):
            media_id = resource["id"]
            image = next(item for item in final["nodes"] if item["id"] == f"compare-input-{index}")
            assert image["metadata"]["storageKey"] == "resource:" + media_id
            assert owner.get(f"{ROOT}/{media_id}/file").content == originals[index]
            assert member.get(f"{ROOT}/{media_id}/file").content == originals[index]
            assert outsider.get(f"{ROOT}/{media_id}/file").status_code == 404
            with resource_app[1]() as session:
                stored = session.get(MediaFile, int(media_id))
                assert minio_bytes(resource_app, stored) == originals[index]
    else:
        for flag in (
            "original_text_editor_used",
            "cleared_input_empty_state",
            "own_content_priority",
        ):
            assert report[flag], flag
        text = next(item for item in final["nodes"] if item["id"] == report["input_node_id"])
        assert text["metadata"]["content"] == report["final_input"]
        assert viewer["metadata"]["content"] == report["final_own_content"]
        shared_viewer = next(item for item in shared["nodes"] if item["id"] == viewer["id"])
        assert shared_viewer["metadata"]["content"] == viewer["metadata"]["content"]
        if kind in {"svg", "html"}:
            assert report["sandbox_isolation"]
        if kind in {"svg", "chart"}:
            assert report["invalid_input_then_recovered"]
        if kind == "chart":
            assert report["csv_bar_and_json_line"] and viewer["metadata"]["chartKind"] == "line"
    assert outsider.get(path + "/my-document").status_code == 404
    with resource_app[1]() as session:
        stored = session.scalar(select(CanvasNode).where(CanvasNode.node_key == viewer["id"]))
        assert stored.kind == kind
        if kind != "compare":
            assert stored.content_json["metadata"]["content"] == report["final_own_content"]
        assert session.scalar(select(AsyncTask.id)) is None
        assert session.scalar(select(CanvasResult.id)) is None
        assert session.scalar(select(CanvasTaskBinding.id)) is None
        assert len(list(session.scalars(select(MediaFile.id)))) == len(media)
    assert database_errors == []
