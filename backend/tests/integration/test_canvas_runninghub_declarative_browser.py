"""声明式协议使用原视频界面及仅 cookies 的原任务恢复。"""

import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_generation_runtime import records
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_runninghub_declarative import (
    CREATE,
    OPTIONS,
    POLL,
    PROVIDER_ID,
    creates,
    declarative_seed,
)
from tests.integration.test_canvas_runninghub_declarative import (
    declarative_runtime as declarative_runtime,
)
from tests.integration.test_identity_collaboration import PASSWORD
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated declarative RunningHub storage",
    ),
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original video UI browser verification",
    ),
]


@pytest.mark.parametrize("unknown", [False, True])
def test_original_video_ui_restores_declarative_task_and_preserves_full_options(
    declarative_runtime, database_errors, tmp_path, unknown
):
    runtime = declarative_runtime
    client, user, project, path, request = declarative_seed(runtime, defaults=OPTIONS)
    initial = client.get(path + "/my-document").json()
    document = initial["source_document"]
    document["nodes"][0].update(
        title="原声明式视频节点",
        position={"x": 550, "y": 200},
        metadata={"content": "", "prompt": "尚未生成的视频来源"},
    )
    committed = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "declarative-ui-" + uuid4().hex},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert committed.status_code == 200, committed.text
    if unknown:
        runtime.state["create"] = "disconnect"
    node = shutil.which("node")
    assert node, "Node.js is required for explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    assert client.post("/api/v1/auth/logout").status_code == 200
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4194"})
    release = tmp_path / "declarative-ui-release"
    stopped = threading.Event()
    errors = []
    executor = GenerationExecutionService(
        runtime.app[1], settings, runtime.gateway, runtime.app[0].state.storage
    )

    def run_worker():
        try:
            while not stopped.wait(0.02):
                with runtime.app[1]() as session:
                    pending = list(
                        session.execute(
                            select(
                                AsyncTask.id, AsyncTask.message_version, AsyncTask.next_action
                            ).where(
                                AsyncTask.initiated_by == int(user["id"]),
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                            )
                        )
                    )
                for identifier, version, action in pending:
                    if action == "poll" and not unknown and not release.exists():
                        continue
                    with runtime.app[1].begin() as session:
                        task = session.get(AsyncTask, identifier)
                        if (
                            task.message_version == version
                            and task.next_run_at
                            and task.next_run_at > utcnow()
                        ):
                            task.next_run_at = utcnow()
                    if stopped.is_set():
                        break
                    executor.execute(identifier, version)
        except BaseException as error:
            errors.append(error)

    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        thread = threading.Thread(target=run_worker, daemon=True)
        thread.start()
        config = {
            "apiUrl": api_url,
            "username": "runninghub_declarative_author",
            "password": PASSWORD,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": request["projectId"],
            "mode": "unknown" if unknown else "declarative",
            "releasePath": str(release),
            "mediaHash": hashlib.sha256(runtime.media).hexdigest(),
            "outputDir": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-video-protocols-python.mjs"],
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
            output, stderr = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, stderr = process.communicate(timeout=10)
            pytest.fail(f"Declarative UI timed out: {stderr}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            thread.join(5)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, indent=2) + "\n", encoding="utf-8"
            )
        assert not thread.is_alive() and not errors, errors
        assert database_errors == [], database_errors
        assert process.returncode == 0, f"Declarative UI failed: {stderr}\n{output}"
        report = json.loads(output)
        assert (
            report["source_saved_before_admission"]
            and report["same_task_after_refresh"]
            and report["fresh_browser_preserved"]
        )
        assert len(report["task_posts"]) == len(creates(runtime)) == 1
        assert report["page_errors"] == [] and report["failed_routes"] == []
        if unknown:
            assert report["unknown_retry_unavailable"]
            assert not any(call["path"] == POLL for call in runtime.calls)
        else:
            assert (
                report["closed_owner_before_completion"]
                and report["real_video_decoded"]
                and report["source_bind_applied"]
            )
            polls = [call for call in runtime.calls if call["path"] == POLL]
            assert len(polls) == 1 and polls[0]["method"] == "GET" and polls[0]["body"] is None
        expected = {
            **{key: value for key, value in OPTIONS.items() if key != "workflow"},
            "prompt": OPTIONS["workflow"],
        }
        assert next(call for call in runtime.calls if call["path"] == CREATE)["body"] == expected
        assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
        with runtime.app[1]() as session:
            record = session.scalar(
                select(AIGenerationRecord).where(
                    AIGenerationRecord.task_id == int(report["task_id"])
                )
            )
            assert record.provider_task_id == (None if unknown else PROVIDER_ID)
            assert (
                record.request_data["canvas_request"]["input"]["metadata"]["providerOptions"][
                    "runninghub-workflow"
                ]
                == OPTIONS
            )
