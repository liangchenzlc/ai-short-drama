"""本人工具启用、固定源隐藏标题栏与原画布恢复；真实HTTP/MySQL/MinIO，不调用模型。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import func, select

from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasResult, MediaFile
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable isolated original editor tool browser/HTTP/MySQL/MinIO verification",
    ),
]


def test_host_activation_and_original_editor_hidden_prompt_header_restore(
    resource_app, database_errors, tmp_path
):
    from types import SimpleNamespace

    runtime = SimpleNamespace(app=resource_app)
    node = shutil.which("node")
    assert node, "Node.js is required for explicit original editor tool browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, user = account(runtime.app, "editor_tool_browser_owner")
    member, member_user = account(runtime.app, "editor_tool_browser_member")
    outsider, _outsider = account(runtime.app, "editor_tool_browser_outsider")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "editor-tool-browser-project"},
        json={"name": "原画布工具状态验证", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document").json()
    assert initial["source_document"]["nodes"] == []
    join(runtime.app, owner, member, project["id"], member_user["id"])
    original = png("red")
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial["source_key"],
            "username": "editor_tool_browser_owner",
            "memberUsername": "editor_tool_browser_member",
            "outsiderUsername": "editor_tool_browser_outsider",
            "password": PASSWORD,
            "image": base64.b64encode(original).decode(),
            "originalPrompt": "人物在窗边阅读，请保留红色参考。",
            "protocol": "no-provider-call",
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-editor-tools-python.mjs"],
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
            pytest.fail(f"Editor tool browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Editor tool browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["task_post_count"] == 0 and report["art_critique_creation_reachable"]
    final = owner.get(path + "/my-document").json()["source_document"]
    target = next(item for item in final["nodes"] if item["id"] == report["target_node_id"])
    assert target["metadata"]["composerContent"] == config["originalPrompt"]
    assert not target["metadata"].get("content") and not target["metadata"].get("taskId")
    with runtime.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
        assert session.scalar(select(func.count()).select_from(AIGenerationRecord)) == 0
        assert session.scalar(select(func.count()).select_from(CanvasResult)) == 0
        media = session.get(MediaFile, int(report["resource_id"]))
        assert media.created_by == int(user["id"]) and media.published_at
        location = ObjectLocation.parse(media.storage_locator, {settings.minio_image_bucket})
        with runtime.app[0].state.storage.open(location.bucket, location.object_name) as response:
            stored = response.read()
        assert stored == original and hashlib.sha256(stored).hexdigest() == report["image_sha256"]
    assert outsider.get(path).status_code == 404
