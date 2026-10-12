"""原宫格/标注故障、保稿及上下文变化，成功请求通过真实API/MySQL/MinIO。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.domain import MediaFile
from short_drama.domain.canvas import CanvasEdge, CanvasNode
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_image_tools_browser import minio_bytes, pixel_pattern_png
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original grid recovery UI with isolated MySQL/MinIO",
    ),
]


@pytest.mark.parametrize("tool", ["grid", "annotation"])
@pytest.mark.parametrize(
    "scenario",
    [
        "upload_failure",
        "commit_failure",
        "conflict",
        "navigation",
        "account_switch",
        "source_deleted",
    ],
)
def test_original_grid_failure_keeps_private_draft_and_recovers_same_children(
    resource_app, database_errors, tmp_path, scenario, tool
):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit grid browser recovery"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    owner, owner_user = account(resource_app, "grid_recovery_owner")
    member, member_user = account(resource_app, "grid_recovery_member")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "grid-recovery-project"},
        json={"name": "宫格故障恢复验证", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document")
    assert initial.status_code == 200, initial.text
    join(resource_app, owner, member, project["id"], member_user["id"])
    other = None
    if scenario == "navigation":
        destination = {
            **initial.json()["source_document"],
            "id": "grid-recovery-destination",
            "canvasTitle": "恢复目标画布",
            "revision": "0",
        }
        response = owner.post(
            f"/api/v1/projects/{project['id']}/canvases",
            headers={"Idempotency-Key": "grid-recovery-other-canvas"},
            json={
                "title": "恢复目标画布",
                "source_key": destination["id"],
                "source_document": destination,
            },
        )
        assert response.status_code == 201, response.text
        other = response.json()
    original = pixel_pattern_png()
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial.json()["source_key"],
            "username": "grid_recovery_owner",
            "memberUsername": "grid_recovery_member",
            "memberId": member_user["id"],
            "password": PASSWORD,
            "image": base64.b64encode(original).decode(),
            "scenario": scenario,
            "tool": tool,
            "otherSourceKey": other["source_key"] if other else None,
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-image-grid-recovery-python.mjs"],
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
            pytest.fail(f"Grid recovery browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Grid recovery failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["scenario"] == scenario and report["tool"] == tool
    child_count = 4 if tool == "grid" else 1
    final = owner.get(path + "/my-document").json()["source_document"]
    if scenario in {"navigation", "account_switch", "source_deleted", "conflict"}:
        assert len(final["nodes"]) == (0 if scenario == "source_deleted" else 1)
        assert final["connections"] == []
        if scenario == "conflict":
            assert report["failure_kept_private_draft"] and report["conflict_draft_preserved"]
            assert final["nodes"][0]["title"] == "成员的新版原图"
            private_ids = [
                item["storageKey"].removeprefix("resource:") for item in report["children"]
            ]
        else:
            assert report["stopped_before_attachment"]
            private_ids = report["private_resources"]
        assert len(private_ids) == child_count
        if other:
            other_path = f"/api/v1/projects/{project['id']}/canvases/{other['id']}/my-document"
            assert owner.get(other_path).json()["source_document"]["nodes"] == []
        with resource_app[1]() as session:
            for resource_id in private_ids:
                media = session.get(MediaFile, int(resource_id))
                assert media.created_by == int(owner_user["id"]) and media.published_at is None
                content = minio_bytes(resource_app, media)
                assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
                assert media.byte_size == len(content)
                assert (
                    member.get(f"/api/v1/canvas-runtime/resources/{resource_id}/file").status_code
                    == 404
                )
            edges = list(
                session.scalars(
                    select(CanvasEdge).where(
                        CanvasEdge.canvas_id == int(project["primary_canvas_id"])
                    )
                )
            )
            assert edges == []
        assert database_errors == []
        return

    assert report["failure_kept_private_draft"] and report["same_children_recovered"]
    assert len(final["nodes"]) == child_count + 1 and len(final["connections"]) == child_count
    assert {node["id"] for node in final["nodes"]} == {
        report["source_node_id"],
        *(item["id"] for item in report["children"]),
    }
    with Image.open(BytesIO(original)) as image:
        original_pixels = image.convert("RGB")
    with resource_app[1]() as session:
        stored = list(
            session.scalars(
                select(CanvasNode).where(CanvasNode.canvas_id == int(project["primary_canvas_id"]))
            )
        )
        assert {item.node_key for item in stored} == {node["id"] for node in final["nodes"]}
        edges = list(
            session.scalars(
                select(CanvasEdge).where(CanvasEdge.canvas_id == int(project["primary_canvas_id"]))
            )
        )
        assert {(edge.from_node_key, edge.to_node_key) for edge in edges} == {
            (report["source_node_id"], item["id"]) for item in report["children"]
        }
        for item in report["children"]:
            child = next(node for node in final["nodes"] if node["id"] == item["id"])
            media_id = int(child["metadata"]["storageKey"].removeprefix("resource:"))
            media = session.get(MediaFile, media_id)
            assert media.created_by == int(owner_user["id"]) and media.published_at
            content = minio_bytes(resource_app, media)
            assert media.byte_size == len(content)
            assert media.checksum_sha256 == item["sha256"] == hashlib.sha256(content).hexdigest()
            with Image.open(BytesIO(content)) as decoded:
                if tool == "grid":
                    row, column = (
                        int(part) - 1 for part in child["title"].split(" · 宫格 ")[1].split("-")
                    )
                    box = (
                        column * 37 // 2,
                        row * 19 // 2,
                        (column + 1) * 37 // 2,
                        (row + 1) * 19 // 2,
                    )
                    assert decoded.convert("RGB").tobytes() == original_pixels.crop(box).tobytes()
                else:
                    assert decoded.size == (37, 19)
                    assert decoded.convert("RGB").getpixel((15, 9)) == (239, 68, 68)
                    assert decoded.convert("RGB").getpixel((36, 18)) == original_pixels.getpixel(
                        (36, 18)
                    )
            assert (
                member.get(f"/api/v1/canvas-runtime/resources/{media_id}/file").content == content
            )
    assert database_errors == []
