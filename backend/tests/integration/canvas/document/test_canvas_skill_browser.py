"""已有 Skill 节点的原 UI 保存、窗口恢复及本人模板隔离。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import AsyncTask
from short_drama.domain.canvas.document.canvas import CanvasNode, CanvasNodeUserState
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original Skill UI with real authenticated HTTP and isolated MySQL",
    ),
]


@pytest.mark.parametrize("width", [1440, 600])
def test_original_skill_snapshot_survives_owner_move_and_cookie_only_recovery(
    identity_app, database_errors, tmp_path, width
):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit original Skill browser verification"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, owner_user = account(identity_app, "skill_browser_owner")
    outsider, _ = account(identity_app, "skill_browser_outsider")
    created = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "skill-browser-project"},
        json={"name": "技能恢复", "workspace_mode": "infinite_canvas"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    document = owner.get(path + "/my-document").json()["source_document"]
    private = {
        "content": "作者私人技能正文",
        "previewContent": "作者私人技能预览",
        "richText": {"type": "doc", "content": [{"type": "text", "text": "作者私人富文本"}]},
        "skillSnapshot": {
            "id": "private-template",
            "name": "本人镜头模板",
            "category": "writing",
            "outputMode": "text",
            "description": "本人安装的镜头写作模板",
            "template": "逐镜头写出人物行动与环境，保持角色设定一致。",
            "outputContract": "仅输出镜头正文。",
            "version": 3,
            "tags": ["分镜", "私人模板"],
        },
    }
    document["nodes"] = [
        {
            "id": "skill-reference",
            "type": "skill",
            "title": "技能引用",
            "position": {"x": -240, "y": -170},
            "width": 320,
            "height": 280,
            "metadata": private,
        },
        {
            "id": "shared-work",
            "type": "text",
            "title": "共享作品",
            "position": {"x": 160, "y": -170},
            "width": 320,
            "height": 220,
            "metadata": {"content": "本人已保存的作品"},
        },
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "skill-browser-snapshot"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    settings = identity_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(identity_app[1], settings) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "skill_browser_owner",
            "password": PASSWORD,
            "width": width,
            "privateMetadata": private,
            "outputDirectory": str(tmp_path / "output" / "playwright"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-skill-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Skill browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Skill browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["width"] == width
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    assert report["owner_drag_saved"]
    assert report["owner_cookie_only_restored"]
    assert report["submissions"] == report["uploads"] == 0
    final = owner.get(path + "/my-document").json()["source_document"]
    assert final["nodes"][0]["metadata"] == private
    assert final["nodes"][0]["position"] == report["final_position"]
    shared = owner.get(path).json()["source_document"]
    assert shared["nodes"][0]["metadata"] == {}
    assert shared["nodes"][1]["metadata"]["content"] == "本人已保存的作品"
    assert outsider.get(path).status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404
    with identity_app[1]() as session:
        assert session.scalar(select(AsyncTask.id)) is None
        stored = session.scalar(select(CanvasNode).where(CanvasNode.node_key == "skill-reference"))
        assert stored.content_json["metadata"] == {}
        states = list(session.scalars(select(CanvasNodeUserState)))
        assert len(states) == 1 and states[0].user_id == int(owner_user["id"])
        assert states[0].draft_json == private
