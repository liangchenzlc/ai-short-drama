"""原深度菜单、私人持久任务和关闭窗口恢复；组件为明确的灰度替身。"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import CanvasLocalJob, MediaFile
from short_drama.service.canvas.timeline.timeline_renderer import CanvasTimelineRenderer
from tests.integration.canvas.depth.test_canvas_depth_broker import independent_depth_worker
from tests.integration.canvas.depth.test_canvas_depth_execution import calls
from tests.integration.canvas.depth.test_canvas_depth_execution import (
    depth_runtime as depth_runtime,
)
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_DEPTH_NATIVE") != "1",
        reason="Enable original depth browser/AMQP/process/MySQL/MinIO verification",
    ),
]


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize("reopen", [False, True], ids=["direct", "cookie-recovery"])
def test_original_depth_menu_keeps_live_edits_and_observes_same_task(
    depth_runtime, tmp_path, width, reopen, drag_surface="body"
):
    runtime = depth_runtime
    node = shutil.which("node")
    assert node
    member, user = account(runtime.app, "depth_browser_member")
    outsider, _ = account(runtime.app, "depth_browser_outsider")
    runtime.document["nodes"] = [
        {
            "id": "media-video",
            "type": "video",
            "title": "深度捕捉来源",
            "position": {"x": -200, "y": -120},
            "width": 400,
            "height": 225,
            "metadata": {
                "status": "success",
                "storageKey": "resource:" + runtime.source["id"],
                "content": f"/api/v1/canvas-runtime/resources/{runtime.source['id']}/file",
                "naturalWidth": 320,
                "naturalHeight": 180,
                "durationMs": 400,
                "mimeType": "video/mp4",
            },
        }
    ]
    committed = runtime.owner.post(
        runtime.document_path + "/commits",
        json={
            "expected_row_version": runtime.document["revision"],
            "source_document": runtime.document,
        },
        headers={"Idempotency-Key": "depth-browser-fixture"},
    )
    assert committed.status_code == 200, committed.text
    (runtime.component / "mode").write_text("hold_until_release")
    runtime.executor.settings = runtime.executor.settings.model_copy(
        update={
            "public_origin": "http://127.0.0.1:4199",
        }
    )
    frontend = Path(__file__).resolve().parents[5] / "frontend/canvas"
    output_directory = tmp_path / "output/playwright"
    with independent_depth_worker(runtime, tmp_path) as broker:
        with isolated_api(
            runtime.app[1], runtime.executor.settings, runtime.app[0].state.storage
        ) as api:
            config = {
                "apiUrl": api,
                "sourceKey": runtime.document["id"],
                "projectId": runtime.project["id"],
                "canvasId": runtime.project["primary_canvas_id"],
                "username": "depth_execution_owner",
                "memberUsername": "depth_browser_member",
                "password": PASSWORD,
                "width": width,
                "reopen": reopen,
                "dragSurface": drag_surface,
                "outputDirectory": str(output_directory),
                "releasePath": str(runtime.component / "release"),
            }
            process = subprocess.Popen(
                [node, "scripts/verify-depth-python.mjs"],
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
                pytest.fail(f"Depth browser timed out: {errors}\n{output}")
            finally:
                (runtime.component / "release").touch()
                if process.poll() is None:
                    stop_browser(process)
                    process.communicate(timeout=10)
            assert process.returncode == 0, f"Depth browser failed: {errors}\n{output}"
            result = json.loads(output)
            assert result["drag_surface"] == drag_surface
            if drag_surface == "title":
                assert len(result["title_drags"]) == (2 if reopen else 1)
                for drag in result["title_drags"]:
                    assert drag["landing"]["button"] is None
                    assert drag["landing"]["icon"] is True
                    assert drag["landing"]["width"] > 0 and drag["landing"]["height"] > 0
                    assert drag["before"] != drag["after"]
                    assert [event["type"] for event in drag["events"]] == [
                        "pointerdown",
                        "mousedown",
                    ]
                    assert all(
                        event["node_id"] == drag["landing"]["node_id"]
                        and event["button"] is None
                        and event["icon"] is True
                        for event in drag["events"]
                    )
            assert result["step"] == "complete" and result["live_edits_preserved"]
            assert bool(result.get("cookie_only_recovery")) == reopen
            assert (
                result["task_posts"] == 1
                and not result["page_errors"]
                and not result["http_errors"]
            )
        broker.evidence.update(
            task_id=result["task_id"], browser_width=width, reopen=reopen, drag_surface=drag_surface
        )
    final = runtime.owner.get(runtime.document_path + "/my-document").json()["source_document"]
    output = next(
        node for node in final["nodes"] if node.get("metadata", {}).get("depthSourceNodeId")
    )
    assert output["title"] == result["live_edits"]["title"] == "处理中保留的新标题"
    assert output["position"] == result["live_edits"]["position"]
    artifact = output_directory / "depth.mp4"
    probe = CanvasTimelineRenderer(runtime.executor.settings).probe(artifact)
    assert (probe.width, probe.height, probe.facts.duration_ms) == (1920, 1080, 400)
    downloaded = runtime.owner.get(f"/api/v1/canvas-runtime/resources/{result['resource_id']}/file")
    assert downloaded.status_code == 200 and downloaded.content == artifact.read_bytes()
    assert hashlib.sha256(downloaded.content).hexdigest() == result["checksum"]
    assert (
        member.get(f"/api/v1/canvas-runtime/resources/{result['resource_id']}/file").status_code
        == 404
    )
    assert (
        outsider.get(f"/api/v1/canvas-runtime/resources/{result['resource_id']}/file").status_code
        == 404
    )
    with runtime.app[1]() as session:
        jobs = list(session.scalars(select(CanvasLocalJob)))
        assert len(jobs) == 1 and jobs[0].attempts == 1 and jobs[0].status == "succeeded"
        assert jobs[0].output_media_id == int(result["resource_id"])
        media = session.get(MediaFile, jobs[0].output_media_id)
        assert media.published_at is not None and media.checksum_sha256 == result["checksum"]
    assert len(calls(runtime)) == 1
