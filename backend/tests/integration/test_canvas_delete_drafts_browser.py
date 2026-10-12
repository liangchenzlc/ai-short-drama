"""真实整图删除未知回执与私人图/绘图草稿；供应商请求未参与本专项。"""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.domain import CanvasDrawing, CanvasDrawingVersion, ProjectCanvas
from short_drama.service.canvas_drawing_document import drawing_document
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_resources import ROOT, canvas
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable isolated MinIO and real canvas browser verification",
    ),
]


@pytest.mark.parametrize("scenario", ["lost_ack", "before_dispatch", "superseded", "revoked"])
def test_unknown_canvas_delete_keeps_private_graph_drawing_drafts_and_original_blobs(
    resource_app, scenario
):
    owner_name, member_name = f"del_draft_owner_{scenario}", f"del_draft_user_{scenario}"
    owner, owner_user = account(resource_app, owner_name)
    member, member_user = account(resource_app, member_name)
    outsider, _ = account(resource_app, f"del_draft_other_{scenario}")
    project, path, document = canvas(owner, "delete-drafts-" + scenario)
    join(resource_app, owner, member, project["id"], member_user["id"])
    sibling = owner.post(
        f"/api/v1/projects/{project['id']}/canvases",
        headers={"Idempotency-Key": "delete-drafts-sibling"},
        json={"title": "保留画布", "source_key": "delete-drafts-sibling-" + scenario},
    )
    assert sibling.status_code == 201, sibling.text
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4189"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-canvas-delete-drafts-python.mjs"],
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
                        "username": member_name,
                        "ownerUsername": owner_name,
                        "password": PASSWORD,
                        "userId": member_user["id"],
                        "ownerId": owner_user["id"],
                        "projectId": project["id"],
                        "path": path,
                        "sourceKey": document["id"],
                        "siblingKey": sibling.json()["source_key"],
                        "scenario": scenario,
                        "runId": uuid4().hex,
                    }
                ),
                timeout=240,
            )
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Delete/drafts browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Delete/drafts browser failed: {errors}\n{output}"
    result = json.loads(output)
    assert result["step"] == "complete" and result["scenario"] == scenario
    assert result["private_graph_and_drawing_drafts_preserved"]
    assert result["drawing_reload_keeps_unknown_draft"]
    assert result["original_blobs_preserved"] and result["no_automatic_write_replay"]
    assert result["account_isolation"] and result["page_errors"] == []
    assert result["drawing_puts"] == 1 and result["drawing_deletes"] == 0
    assert result["blob_count"] >= 4
    assert len(result["deletes"]) == (2 if scenario == "before_dispatch" else 1)
    with resource_app[1](info={"system_access": True}) as session:
        drawing = session.scalar(
            select(CanvasDrawing).where(
                CanvasDrawing.canvas_id == int(project["primary_canvas_id"]),
                CanvasDrawing.source_key == result["drawing_id"],
            )
        )
        assert drawing is not None and drawing.archived_at is None
        assert str(drawing.row_version) == result["drawing_revision"]
        versions = list(
            session.scalars(
                select(CanvasDrawingVersion).where(CanvasDrawingVersion.drawing_id == drawing.id)
            )
        )
        assert len(versions) == 1
        assert versions[0].document_json["$snapshot_encoding"] == "json"
        assert drawing_document(versions[0])["snapshot"] == result["original_snapshot"]
        canonical = session.get(ProjectCanvas, int(project["primary_canvas_id"]))
        assert (canonical.archived_at is None) == (scenario == "superseded")
        assert canonical.row_version == int(result["canvas_revision"]) + (
            2 if scenario == "superseded" else 1
        )
    for media in result["original_media"]:
        response = owner.get(f"{ROOT}/{media['id']}/file")
        assert response.status_code == 200, response.text
        assert response.content == base64.b64decode(media["bytes"])
        assert outsider.get(f"{ROOT}/{media['id']}/file").status_code == 404
        if scenario == "revoked":
            assert member.get(f"{ROOT}/{media['id']}/file").status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404
    if scenario == "superseded":
        assert member.get(path + "/my-document").status_code == 200
    else:
        assert member.get(path + "/my-document").status_code == 404
