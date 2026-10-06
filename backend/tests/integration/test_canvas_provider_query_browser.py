"""真实原取回按钮、Python/MySQL/MinIO、可播放视频与刷新成员共享；供应商受控。"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import func, select

from short_drama.ai import GenerationGateway
from short_drama.api.v1 import canvas_provider_tasks
from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasResult, MediaFile
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_generation_media_runtime import playable_fixture
from tests.integration.test_canvas_generation_runtime import TASKS, admit, seed
from tests.integration.test_canvas_provider_query import FROZEN_KEY, PROVIDER_ID, update_key
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real original-provider browser/Python/MySQL/MinIO verification",
    ),
]


def test_original_failure_button_recovers_plays_and_shares_the_same_video(
    resource_app, monkeypatch, tmp_path
):
    node = shutil.which("node")
    assert node, "Node.js is required for explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    author, user, project, path, request = seed(
        resource_app, "canvas_query_browser_author", kind="video"
    )
    member, reader = account(resource_app, "canvas_query_browser_reader")
    join(resource_app, author, member, project["id"], reader["id"])
    update_key(author, request["logicalModelId"], FROZEN_KEY)
    initial = author.get(path + "/my-document").json()
    document = initial["source_document"]
    document["nodes"][0]["position"] = {"x": 420, "y": 180}
    document["nodes"][0]["title"] = "原视频取回验证"
    document["nodes"][0]["metadata"]["content"] = ""
    seeded = author.post(
        path + "/commits",
        headers={"Idempotency-Key": "browser-query-source"},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert seeded.status_code == 200, seeded.text
    task = admit(author, request)
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4193"})
    content = playable_fixture(tmp_path, "video")
    gateway = GenerationGateway(settings)
    network = []

    def response(method, url, _snapshot, headers, body, **_options):
        assert headers["Authorization"] == "Bearer " + FROZEN_KEY
        network.append((method, url))
        if method == "POST":
            assert url.endswith("/contents/generations/tasks")
            assert body["model"] == request["model"]
            return {"id": PROVIDER_ID, "status": "queued"}
        assert method == "GET" and body is None
        assert url.endswith("/contents/generations/tasks/" + PROVIDER_ID)
        return {
            "id": PROVIDER_ID,
            "status": "failed" if len(network) == 2 else "succeeded",
            "content": {"video_url": "https://controlled-media.example.test/original.mp4"},
        }

    def download(url, limit):
        assert url == "https://controlled-media.example.test/original.mp4"
        assert limit >= len(content)
        return content, "video/mp4"

    monkeypatch.setattr(gateway, "_json_request", response)
    monkeypatch.setattr(gateway, "download_media", download)
    monkeypatch.setattr(canvas_provider_tasks, "GenerationGateway", lambda _settings: gateway)
    executor = GenerationExecutionService(
        resource_app[1], settings, gateway, resource_app[0].state.storage
    )
    executor.execute(task["id"], 1)
    with resource_app[1]() as session:
        pending = session.get(AsyncTask, int(task["id"]))
        assert pending.next_action == "poll"
        version = pending.message_version
    executor.execute(task["id"], version)
    failed = author.get(f"{TASKS}/{task['id']}")
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    assert failed.json()["providerRequestId"] == PROVIDER_ID
    assert len(network) == 2
    assert author.post("/api/v1/auth/logout").status_code == 200
    assert member.post("/api/v1/auth/logout").status_code == 200
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        process = subprocess.Popen(
            [node, "scripts/verify-provider-query-python.mjs"],
            cwd=frontend,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        config = {
            "apiUrl": api_url,
            "author": "canvas_query_browser_author",
            "member": "canvas_query_browser_reader",
            "password": PASSWORD,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": initial["source_key"],
            "taskId": task["id"],
            "nodeId": request["input"]["metadata"]["nodeId"],
            "actorId": user["id"],
            "memberId": reader["id"],
        }
        try:
            output, errors = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Provider query browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Provider query browser failed: {errors}\n{output}"
        result = json.loads(output)
        assert result["source_button_reachable"] and result["source_button_query_count"] == 1
        assert result["source_bind_applied"] and result["persisted_after_fresh_browser"]
        assert result["author_video_played"] and result["fresh_video_played"]
        assert result["member_video_played"] and result["private_task_hidden_from_member"]
        assert result["task_post_count"] == 0 and not result["page_errors"]
        assert all(
            route["status"] == 404
            and route["path"]
            in {
                "/api/v1/canvas-runtime/assistant/status",
                "/api/v1/canvas-runtime/assistant/ui-session",
            }
            for route in result["failed_routes"]
        )
    assert [method for method, _ in network] == ["POST", "GET", "GET"]
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
        assert session.scalar(select(func.count()).select_from(AIGenerationRecord)) == 1
        assert session.get(AsyncTask, int(task["id"])).status == "succeeded"
        media = session.scalar(select(MediaFile).where(MediaFile.format_code == "video/mp4"))
        assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
        result = session.scalar(select(CanvasResult))
        assert result.attachment_status == "attached" and result.media_id == media.id
