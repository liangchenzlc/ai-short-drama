"""原版绘图保存合同在真实 Python/MySQL/MinIO 中运行。"""

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import CanvasDrawing, CanvasDrawingVersion
from tests.integration.test_canvas_resources import ROOT, account, canvas, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real isolated drawing resource verification",
    ),
]


def drawing_path(document, key="sketch"):
    return f"/api/v1/canvas-runtime/canvas-projects/{document['id']}/drawings/{key}"


def drawing_payload(revision="0", **values):
    return {
        "drawing": {
            "drawingId": "sketch",
            "engine": "excalidraw",
            "revision": revision,
            "snapshot": {
                "elements": [{"id": "shape", "type": "rectangle", "y": 10.399993896484375}],
                "files": {},
            },
            "shapeCount": 1,
            "pageCount": 1,
            **values,
        }
    }


def test_drawing_save_cas_replay_shared_media_and_tombstone(resource_app):
    owner, _ = account(resource_app, "drawing_owner")
    member, user = account(resource_app, "drawing_member")
    outsider, _ = account(resource_app, "drawing_outsider")
    project, _, document = canvas(owner, "drawing-project")
    join(resource_app, owner, member, project["id"], user["id"])
    resource = upload(owner, png(), source=document["id"]).json()["resource"]
    path = drawing_path(document)
    assert owner.get(path).status_code == 404
    request = drawing_payload(
        previewResourceId=resource["id"],
        render={"resourceId": resource["id"], "background": "white", "pageId": "page"},
    )
    created = owner.put(path, json=request)
    assert created.status_code == 200, created.text
    saved = created.json()["drawing"]
    assert saved["revision"] == "1"
    assert owner.get(path).json()["drawing"]["snapshot"] == request["drawing"]["snapshot"]
    assert saved["render"]["storageKey"] == f"resource:{resource['id']}"
    assert owner.put(path, json=request).json() == created.json()
    assert member.get(path).json() == created.json()
    assert outsider.get(path).status_code == 404
    assert outsider.put(path, json=request).status_code == 404
    assert member.get(f"{ROOT}/{resource['id']}/file").content == png()
    changed = deepcopy(request)
    changed["drawing"]["snapshot"]["elements"][0]["type"] = "ellipse"
    conflict = member.put(path, json=changed)
    assert conflict.status_code == 409, conflict.text
    changed["drawing"]["revision"] = "1"
    updated = member.put(path, json=changed)
    assert updated.status_code == 200, updated.text
    assert updated.json()["drawing"]["revision"] == "2"
    assert owner.get(path + "/versions/1").json()["drawing"] == saved
    assert owner.get(path.rsplit("/", 1)[0]).json()["drawings"][0]["revision"] == "2"
    deleted = member.delete(path)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"id": "sketch", "revision": "2"}
    assert member.delete(path).json() == deleted.json()
    assert owner.get(path).status_code == 404
    assert owner.get(path + "/versions/1").json()["drawing"] == saved
    assert owner.get(path.rsplit("/", 1)[0]).json() == {"drawings": []}
    rejected = owner.put(path, json=request)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_drawing_deleted"


def test_drawing_rejects_foreign_resources_and_invalid_contract(resource_app):
    owner, _ = account(resource_app, "drawing_guard")
    _, _, document = canvas(owner, "drawing-guard")
    _, _, other_document = canvas(owner, "drawing-other")
    foreign = upload(owner, png(), source=other_document["id"]).json()["resource"]
    path = drawing_path(document)
    invalid = owner.put(path, json=drawing_payload(previewResourceId=foreign["id"]))
    assert invalid.status_code == 404, invalid.text
    assert owner.get(path).status_code == 404
    for values in (
        {"engine": "unknown"},
        {"revision": "18446744073709551615"},
        {"snapshot": {"apiKey": "test-not-a-secret"}},
        {"snapshot": {"files": {"file": {"dataURL": "blob:page-only"}}}},
        {"render": {"background": "transparent"}},
        {"drawingId": "another"},
    ):
        rejected = owner.put(path, json=drawing_payload(**values))
        assert rejected.status_code == 422, (values, rejected.text)
    request = drawing_payload()
    del request["drawing"]["revision"]
    assert owner.put(path, json=request).status_code == 422
    assert owner.get(path).status_code == 404


def test_drawing_big_versions_and_concurrent_cas_keep_both_saved_versions(resource_app):
    owner, _ = account(resource_app, "drawing_versions")
    member, user = account(resource_app, "drawing_versions_member")
    project, _, document = canvas(owner, "drawing-versions")
    join(resource_app, owner, member, project["id"], user["id"])
    path = drawing_path(document)
    assert owner.put(path, json=drawing_payload()).status_code == 200
    large = 9007199254740993
    with resource_app[1].begin() as session:
        drawing = session.scalar(
            select(CanvasDrawing).where(
                CanvasDrawing.canvas_id == int(project["primary_canvas_id"])
            )
        )
        version = session.scalar(
            select(CanvasDrawingVersion).where(CanvasDrawingVersion.drawing_id == drawing.id)
        )
        drawing.row_version = version.row_version = large
    assert owner.get(path).json()["drawing"]["revision"] == str(large)
    request = drawing_payload(str(large))
    other = deepcopy(request)
    request["drawing"]["snapshot"]["elements"][0]["x"] = 10
    other["drawing"]["snapshot"]["elements"][0]["x"] = 20
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(owner.put, path, json=request)
        second = executor.submit(member.put, path, json=other)
        responses = [first.result(), second.result()]
    assert sorted(response.status_code for response in responses) == [200, 409]
    accepted = next(
        response.json()["drawing"] for response in responses if response.status_code == 200
    )
    assert accepted["revision"] == str(large + 1)
    assert owner.get(path).json()["drawing"] == accepted
    previous = owner.get(path + f"/versions/{large}").json()["drawing"]
    assert "x" not in previous["snapshot"]["elements"][0]


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
    reason="Enable explicit real browser verification",
)
def test_drawing_original_editor_with_real_storage(resource_app):
    from tests.integration.test_canvas_browser import isolated_api, stop_browser

    client, _ = account(resource_app, "canvas_drawing_browser")
    client.post("/api/v1/auth/logout")
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4189"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-drawing-python.mjs"],
            cwd=Path(__file__).resolve().parents[3] / "frontend" / "canvas",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(
                json.dumps(
                    {
                        "apiUrl": api_url,
                        "username": "canvas_drawing_browser",
                        "password": PASSWORD,
                    }
                ),
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Drawing browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Drawing browser failed: {errors}\n{output}"
        result = json.loads(output)
        assert result["pointer_drawing_saved"] and result["lost_ack_replayed"]
        assert result["fresh_context_restored"] and result["conflict_kept_draft"]
        assert result["history_preview_frozen"] and result["history_restore_replayed"]
        assert result["history_deleted_drawing_restored"]
        assert result["cross_browser_deleted_drawing_restored"]
        assert result["stale_read_kept_deletion"]
        assert result["stale_bundle_and_copy_kept_deletion"]
        assert result["newer_restored_bundle_available"]
        assert result["drawing_same_project_pasted"] and result["drawing_cross_project_pasted"]
        assert result["drawing_parameter_variant_saved"]
        assert result["drawing_variant_deleted_independently"]
        assert result["drawing_project_group_copied_atomically"]
        assert result["drawing_whole_canvas_copied"] and result["drawing_readonly_project_copied"]
        assert result["drawing_project_copy_fresh_context_restored"]
        assert result["concurrent_node_copy_retains_one_drawing_version"]
        assert result["page_errors"] == []
