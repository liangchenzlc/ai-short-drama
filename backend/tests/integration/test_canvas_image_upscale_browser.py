"""固定源调整尺寸原UI；真实HTTP/MySQL/MinIO与指定失败、迟到响应。"""

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

from short_drama.domain import AsyncTask, MediaFile
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
        reason="Enable original upscale UI with isolated MySQL/MinIO",
    ),
]


def assert_upstream_pixels(content: bytes, reference: bytes, size: tuple[int, int]) -> None:
    """精确对照固定源在同一Chrome的结果，不把Pillow的取样边界作为源行为。"""
    with Image.open(BytesIO(content)) as saved, Image.open(BytesIO(reference)) as upstream:
        assert saved.format == "PNG" and saved.size == upstream.size == size
        assert saved.convert("RGBA").tobytes() == upstream.convert("RGBA").tobytes()


@pytest.mark.parametrize(
    "scenario",
    [
        "normal_nearest",
        "normal_bilinear",
        "normal_high",
        "normal_bilinear_2k",
        "normal_high_4k",
        "max_source",
        "upload_failure",
        "commit_failure",
        "conflict",
        "navigation",
        "account_switch",
        "source_deleted",
    ],
)
def test_original_upscale_ui_bytes_restore_and_failure_drafts(
    resource_app, database_errors, tmp_path, scenario
):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit upscale browser verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    upstream_root = os.environ.get("BEEFTV_ROOT")
    assert upstream_root, "Set BEEFTV_ROOT to the read-only fixed-source repository"
    manifest = json.loads((frontend / "source-manifest.json").read_text(encoding="utf-8"))
    upstream_image_source = subprocess.check_output(
        [
            "git",
            "-C",
            upstream_root,
            "show",
            f"{manifest['commit']}:web/src/lib/canvas/canvas-image-data.ts",
        ]
    )
    upstream_sha256 = manifest["files"]["src/lib/canvas/canvas-image-data.ts"]["upstream_sha256"]
    assert hashlib.sha256(upstream_image_source).hexdigest() == upstream_sha256
    target_long_edge = (
        2048 if scenario.endswith("_2k") else 4096 if scenario.endswith("_4k") else 1024
    )
    output_size = (target_long_edge, {1024: 526, 2048: 1052, 4096: 2103}[target_long_edge])
    owner, owner_user = account(resource_app, "upscale_browser_owner")
    member, member_user = account(resource_app, "upscale_browser_member")
    outsider, _ = account(resource_app, "upscale_browser_outsider")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "upscale-browser-project"},
        json={"name": "原图放大验证", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
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
            "id": "upscale-browser-destination",
            "canvasTitle": "恢复目标画布",
            "revision": "0",
        }
        response = owner.post(
            f"/api/v1/projects/{project['id']}/canvases",
            headers={"Idempotency-Key": "upscale-browser-other-canvas"},
            json={
                "title": "恢复目标画布",
                "source_key": destination["id"],
                "source_document": destination,
            },
        )
        assert response.status_code == 201, response.text
        other = response.json()
    original = pixel_pattern_png()
    if scenario == "max_source":
        output = BytesIO()
        Image.new("RGB", (4096, 7), (29, 83, 157)).save(output, "PNG")
        original = output.getvalue()
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial.json()["source_key"],
            "username": "upscale_browser_owner",
            "memberUsername": "upscale_browser_member",
            "memberId": member_user["id"],
            "password": PASSWORD,
            "image": base64.b64encode(original).decode(),
            "scenario": scenario,
            "algorithm": scenario.split("_")[1] if scenario.startswith("normal_") else "nearest",
            "targetLongEdge": target_long_edge,
            "tool": "upscale",
            "otherSourceKey": other["source_key"] if other else None,
            "outputDirectory": str(tmp_path / "browser"),
            "upstreamCommit": manifest["commit"],
            "upstreamImageSource": upstream_image_source.decode("utf-8"),
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
            pytest.fail(f"Upscale browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Upscale browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["page_errors"] == []
    assert report["scenario"] == scenario and report["tool"] == "upscale"
    assert report["desktop_upscale_hidden"]
    final = owner.get(path + "/my-document").json()["source_document"]
    assert outsider.get(path + "/my-document").status_code == 404
    with resource_app[1]() as session:
        assert (
            session.scalar(select(AsyncTask.id).where(AsyncTask.project_id == int(project["id"])))
            is None
        )
    if scenario == "max_source":
        assert report["cancel_kept_source"] and report["max_source_disabled"]
        assert len(final["nodes"]) == 1 and final["connections"] == []
        assert report["uploads"] == 1
        assert database_errors == []
        return

    reference = (tmp_path / "browser" / "upstream-image.png").read_bytes()
    assert report["upstream_commit"] == manifest["commit"]
    assert report["upstream_source_sha256"] == upstream_sha256
    assert report["upstream_image_sha256"] == hashlib.sha256(reference).hexdigest()

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
        assert len(private_ids) == 1
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
                assert_upstream_pixels(content, reference, output_size)
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

    if scenario.startswith("normal_"):
        assert report["cancel_kept_source"] and report["fresh_context_restored"]
    else:
        assert report["failure_kept_private_draft"] and report["same_children_recovered"]
    assert len(final["nodes"]) == 2 and len(final["connections"]) == 1
    source = next(item for item in final["nodes"] if item["id"] == report["source_node_id"])
    child = next(item for item in final["nodes"] if item["id"] != source["id"])
    assert child["id"] == report["children"][0]["id"]
    assert child["title"] == source["title"] + " · 放大"
    assert child["position"] == {
        "x": source["position"]["x"] + source["width"] + 96,
        "y": source["position"]["y"],
    }
    assert child["metadata"]["resultOrigin"] == "derived"
    assert child["metadata"]["generatedFromNodeId"] == source["id"]
    assert (child["metadata"]["naturalWidth"], child["metadata"]["naturalHeight"]) == output_size
    with resource_app[1]() as session:
        stored = list(
            session.scalars(
                select(CanvasNode).where(CanvasNode.canvas_id == int(project["primary_canvas_id"]))
            )
        )
        assert {item.node_key for item in stored} == {source["id"], child["id"]}
        edges = list(
            session.scalars(
                select(CanvasEdge).where(CanvasEdge.canvas_id == int(project["primary_canvas_id"]))
            )
        )
        assert [(edge.from_node_key, edge.to_node_key) for edge in edges] == [
            (source["id"], child["id"])
        ]
        for item in final["nodes"]:
            media_id = item["metadata"]["storageKey"].removeprefix("resource:")
            media = session.get(MediaFile, int(media_id))
            assert media.created_by == int(owner_user["id"]) and media.published_at
            content = minio_bytes(resource_app, media)
            assert media.byte_size == len(content)
            assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
            if item["id"] == source["id"]:
                assert content == original
            else:
                assert media.checksum_sha256 == report["children"][0]["sha256"]
                assert (media.width, media.height) == output_size
                assert_upstream_pixels(content, reference, output_size)
            resource_path = f"/api/v1/canvas-runtime/resources/{media_id}/file"
            assert owner.get(resource_path).content == member.get(resource_path).content == content
            assert outsider.get(resource_path).status_code == 404
    assert database_errors == []
