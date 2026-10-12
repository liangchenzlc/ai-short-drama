"""原音频界面保存、生成、播放与关闭窗口后仅cookies恢复。"""

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

from short_drama.ai.canvas_audio_adapters import ASYNC_AUDIO
from short_drama.domain import AIGenerationRecord, AsyncTask, MediaFile
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_audio_protocols import (
    PROVIDER_ID,
    audio_seed,
    submissions,
)
from tests.integration.test_canvas_audio_protocols import (
    audio_runtime as audio_runtime,
)
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_generation_runtime import TASKS, WORKSPACE, records
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app
from tests.unit.test_canvas_audio_formats import MIMES, encoded_audio

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original audio UI/Python/MySQL/MinIO verification",
    ),
]


@pytest.mark.parametrize(
    ("protocol", "format", "saved_defaults"),
    [
        pytest.param(protocol, format, None, id=f"{protocol}-{format}")
        for protocol in ("async-audio", "openai-audio")
        for format in MIMES
    ]
    + [
        pytest.param(
            protocol,
            "mp3",
            {"instructions": "保存的原界面旁白指令"},
            id=f"{protocol}-saved-defaults",
        )
        for protocol in ("async-audio", "openai-audio")
    ]
    + [pytest.param("unknown", "mp3", None, id="unknown-mp3")],
)
def test_original_audio_ui_restores_the_same_task_and_plays_real_archived_bytes(
    audio_runtime, tmp_path, protocol, format, saved_defaults
):
    runtime = audio_runtime
    if format != "mp3":
        runtime.media = encoded_audio(tmp_path, format).read_bytes()
        runtime.state["media"] = runtime.media
    if format == "pcm":
        runtime.state["mime"] = "audio/pcm"
    node = shutil.which("node")
    assert node, "Node.js is required for explicit browser integration"
    frontend = Path(__file__).resolve().parents[3] / "frontend" / "canvas"
    assert (frontend / "node_modules" / "playwright").is_dir()
    assert (frontend.parent / "dist" / "canvas-app" / "index.html").is_file()
    client, user, project, path, request = audio_seed(
        runtime,
        protocol="openai-audio" if protocol == "openai-audio" else "async-audio",
        defaults=saved_defaults,
    )
    request["input"]["config"]["audioFormat"] = format
    if saved_defaults is not None:
        request["input"]["config"]["audioInstructions"] = ""
    if protocol == "unknown":
        runtime.state["create"] = "lost-ack"
    workspace = client.get(WORKSPACE).json()
    preferences = client.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                **request["input"]["config"],
                "model": f"host-{request['logicalModelId']}::{request['model']}",
            },
        },
    )
    assert preferences.status_code == 200, preferences.text
    initial = client.get(path + "/my-document").json()
    document = initial["source_document"]
    document["nodes"] = [
        {
            "id": "runtime-node",
            "type": "audio",
            "title": "原音频生成节点",
            "position": {"x": 300, "y": 230},
            "width": 320,
            "height": 220,
            "metadata": {"content": "", "prompt": "尚未生成的旁白来源"},
        }
    ]
    document["connections"] = []
    committed = client.post(
        path + "/commits",
        headers={"Idempotency-Key": "browser-audio-" + uuid4().hex},
        json={"expected_row_version": initial["row_version"], "source_document": document},
    )
    assert committed.status_code == 200, committed.text
    assert client.post("/api/v1/auth/logout").status_code == 200
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    release_path = tmp_path / "audio-original-release"
    stopped = threading.Event()
    worker_errors = []
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
                    if action != "submit" and not release_path.exists():
                        continue
                    if stopped.is_set():
                        break
                    executor.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(error)

    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker = threading.Thread(target=run_worker, name="canvas-audio-test-driver", daemon=True)
        worker.start()
        config = {
            "apiUrl": api_url,
            "username": "audio_protocol_author",
            "password": PASSWORD,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": request["projectId"],
            "protocol": protocol,
            "format": format,
            "releasePath": str(release_path),
            "outputDirectory": str(tmp_path / "browser"),
            "mediaHash": hashlib.sha256(runtime.media).hexdigest(),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-audio-protocols-python.mjs"],
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
            pytest.fail(f"Audio browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            worker.join(5)
        assert not worker.is_alive(), "Task-owned audio driver did not stop"
        assert not worker_errors, worker_errors
        assert process.returncode == 0, f"Audio browser failed: {errors}\n{output}"
        report = json.loads(output)
    assert report["step"] == "complete"
    assert report["source_saved_before_admission"] and report["closed_owner_before_completion"]
    assert report["same_task_recovered"] and report["fresh_report_restored"]
    assert report["page_errors"] == [] and report["failed_routes"] == []
    assert len(report["task_posts"]) == 1 and len(submissions(runtime)) == 1
    assert report["task_posts"][0]["config"]["audioFormat"] == format
    assert submissions(runtime)[0]["body"]["response_format"] == format
    if saved_defaults is not None:
        assert report["task_posts"][0]["provider_options"][protocol] == saved_defaults
        assert submissions(runtime)[0]["body"]["instructions"] == saved_defaults["instructions"]
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    logged = client.post(
        "/api/v1/auth/login", json={"username": "audio_protocol_author", "password": PASSWORD}
    )
    assert logged.status_code == 200, logged.text
    client.headers["X-CSRF-Token"] = client.cookies["sd_csrf"]
    completed = client.get(f"{TASKS}/{report['task_id']}").json()
    with runtime.app[1]() as session:
        call = session.scalar(select(AIGenerationRecord))
        assert call.adapter == ("openai_speech.v1" if protocol == "openai-audio" else ASYNC_AUDIO)
        assert (
            call.request_data["canvas_request"]["input"]["prompt"]
            == report["task_posts"][0]["prompt"]
        )
        assert call.provider_task_id == (PROVIDER_ID if protocol == "async-audio" else None)
        media = session.scalar(select(MediaFile))
        if protocol == "unknown":
            assert call.status == "unknown" and media is None
        else:
            assert call.status == "succeeded"
            assert media.checksum_sha256 == config["mediaHash"]
            assert media.format_code == MIMES[format]
            assert (
                media.storage_locator.startswith("minio://canvas-test-")
                and "-audio/" in media.storage_locator
            )
    member, member_user = account(runtime.app, "audio_browser_member")
    outsider, outsider_user = account(runtime.app, "audio_browser_outsider")
    join(runtime.app, client, member, project["id"], member_user["id"])
    member.headers["X-Canvas-Actor"] = member_user["id"]
    outsider.headers["X-Canvas-Actor"] = outsider_user["id"]
    for reader in (member, outsider):
        assert reader.get(f"{TASKS}/{report['task_id']}").status_code == 404
        assert reader.get(f"{TASKS}/{report['task_id']}/logs").status_code == 404
    if protocol == "unknown":
        assert report["unknown_retry_unavailable"] and completed["status"] == "failed"
        assert not completed["canRetry"] and not completed["canResume"]
        return
    assert report["original_play_clicked"]
    if format == "pcm":
        assert report["pcm_decode_error_shown"] and not report["real_audio_decoded"]
    else:
        assert report["real_audio_decoded"] and not report["pcm_decode_error_shown"]
    assert completed["status"] == "succeeded"
    result = json.loads(completed["resultJson"])["audio"]
    assert client.get(result["url"]).content == runtime.media
    saved = client.get(path + "/my-document").json()["source_document"]
    assert (
        next(item for item in saved["nodes"] if item["id"] == report["node_id"])["metadata"][
            "storageKey"
        ]
        == result["storageKey"]
    )
    assert (
        next(item for item in saved["nodes"] if item["id"] == "runtime-node")["metadata"]["prompt"]
        == report["task_posts"][0]["prompt"]
    )
    assert member.get(result["url"]).content == runtime.media
    assert outsider.get(result["url"]).status_code == 404
