"""真实 FFmpeg/子进程/MySQL/MinIO 深度归档；组件替身不执行深度模型。"""

import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from short_drama.domain import AsyncTask, CanvasLocalJob, CanvasResourceUpload, MediaFile
from short_drama.service.canvas_depth_archive import archive_key
from short_drama.tasks.canvas_local import CanvasLocalExecutor
from tests.integration.test_canvas_resources import canvas, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_timeline_ffmpeg import ffmpeg
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_DEPTH_NATIVE") != "1",
        reason="Enable isolated native process/FFmpeg/MySQL/MinIO depth checks",
    ),
]
PATH = "/api/v1/canvas-runtime"

# 明确的组件替身：检查原CLI参数并产真实灰度H.264；不声称模型推理。
COMPONENT = r"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
root = Path(__file__).resolve().parents[2]
mode = (root / "mode").read_text()
parser = argparse.ArgumentParser()
parser.add_argument("input")
for name in ("output-dir", "runtime-dir", "device", "input-size", "max-resolution",
             "output-resolution", "max-seconds", "low-percentile", "high-percentile", "gamma"):
    parser.add_argument("--" + name, required=True)
args = parser.parse_args()
assert (args.input_size, args.max_resolution, args.output_resolution, args.max_seconds,
        args.low_percentile, args.high_percentile, args.gamma) == (
        "280", "960", "1920x1080", "15", "2", "98", "1.25")
with (root / "calls.jsonl").open("a", encoding="utf-8") as record:
    record.write(json.dumps(vars(args)) + "\n")
sys.stdout.write("[2/3] controlled component\n")
sys.stdout.flush()
if mode == "hold":
    (root / "entered").touch()
    time.sleep(60)
if mode == "hold_until_release":
    (root / "entered").touch()
    deadline = time.monotonic() + 90
    while not (root / "release").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("controlled browser release was not received")
        time.sleep(0.05)
if mode == "failure":
    sys.stderr.write("controlled component failure\n")
    raise SystemExit(1)
output = Path(args.output_dir) / (Path(args.input).stem + "_depth_preview.mp4")
if mode == "corrupt":
    output.write_bytes(b"not video")
else:
    size = "320:180" if mode == "wrong_size" else "1920:1080"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", args.input, "-an",
        "-vf", "format=gray,scale=" + size, "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output)], check=True)
sys.stdout.write("[3/3] controlled output\n")
"""
PROBE = 'import sys\nsys.stderr.write("controlled CUDA unavailable\\n")\nraise SystemExit(42)\n'


@pytest.fixture
def depth_runtime(resource_app, tmp_path):
    app, factory, current = resource_app
    worker = tmp_path / "component/worker"
    package = worker / "depth_capture"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(COMPONENT, encoding="utf-8")
    (package / "probe.py").write_text(PROBE, encoding="utf-8")
    (worker.parent / "mode").write_text("success", encoding="utf-8")
    model = tmp_path / "model"
    model.mkdir()
    settings = current.model_copy(
        update={
            "beeftv_depth_python": sys.executable,
            "beeftv_depth_tool_dir": str(worker),
            "beeftv_depth_runtime": str(model),
            "render_scratch_root": str(tmp_path / "scratch"),
        }
    )
    app.state.settings = settings
    owner, user = account(resource_app, "depth_execution_owner")
    project, document_path, document = canvas(owner, "depth-execution-project")
    source_path = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=5:duration=0.4",
        "-pix_fmt",
        "yuv420p",
        source_path,
    )
    content = source_path.read_bytes()
    response = upload(
        owner,
        content,
        source=document["id"],
        key="depth-source",
        kind="video",
        name="source.mp4",
        mime="video/mp4",
    )
    assert response.status_code == 200, response.text
    source = response.json()["resource"]
    body = {
        "projectId": document["id"],
        "resourceId": source["id"],
        "clientOperationId": "depth-execution-operation",
    }
    return SimpleNamespace(
        owner=owner,
        user=user,
        project=project,
        document_path=document_path,
        document=document,
        source=source,
        body=body,
        content=content,
        executor=CanvasLocalExecutor(factory, settings, app.state.storage),
        app=resource_app,
        component=worker.parent,
        tmp=tmp_path,
    )


def create(runtime, **patch):
    response = runtime.owner.post(PATH + "/depth-captures", json={**runtime.body, **patch})
    assert response.status_code == 202, response.text
    return response.json()


def detail(runtime, task):
    response = runtime.owner.get(PATH + f"/tasks/{task['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def calls(runtime):
    path = runtime.component / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def clean(runtime):
    assert list(Path(runtime.executor.settings.render_scratch_root).glob("beeftv-depth-*")) == []


def test_depth_process_archives_private_bytes_and_cookie_only_restore(depth_runtime):
    runtime = depth_runtime
    task = create(runtime)
    runtime.executor.execute(task["id"], "1")
    state = detail(runtime, task)
    assert state["status"] == "succeeded" and state["progress"] == 100, state
    result = json.loads(state["resultJson"])
    assert result["fileName"] == "depth-action-reference.mp4"
    assert (result["width"], result["height"], result["durationMs"]) == (1920, 1080, 400)
    assert result["device"] == "cpu" and result["runtimeVersion"] == "v1"
    resource = runtime.owner.get(PATH + f"/resources/{result['resourceId']}").json()["resource"]
    assert resource["width"] == 1920 and resource["height"] == 1080
    content = runtime.owner.get(PATH + f"/resources/{result['resourceId']}/file").content
    assert len(content) == result["size"] and content != runtime.content
    runtime.executor.execute(task["id"], "1")
    assert len(calls(runtime)) == 1
    fresh = TestClient(runtime.app[0], headers={"Origin": runtime.app[2].public_origin})
    try:
        fresh.cookies.update(runtime.owner.cookies)
        assert fresh.get(PATH + f"/tasks/{task['id']}").json() == state
    finally:
        fresh.close()
    member, user = account(runtime.app, "depth_execution_member")
    join(runtime.app, runtime.owner, member, runtime.project["id"], user["id"])
    assert member.get(PATH + f"/tasks/{task['id']}").status_code == 404
    assert member.get(PATH + f"/resources/{result['resourceId']}/file").status_code == 404
    with runtime.app[1]() as session:
        job = session.get(CanvasLocalJob, int(task["id"]))
        media = session.get(MediaFile, int(result["resourceId"]))
        upload = session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.media_id == media.id)
        )
        assert job.output_media_id == media.id and job.attempts == 1
        assert media.published_at is None and media.created_by == int(runtime.user["id"])
        assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
        assert upload.idempotency_hash == hashlib.sha256(archive_key(job.id).encode()).hexdigest()
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 2
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
    clean(runtime)


@pytest.mark.parametrize("mode", ["failure", "wrong_size", "corrupt"])
def test_component_failure_is_durable_without_output_media(depth_runtime, mode):
    runtime = depth_runtime
    (runtime.component / "mode").write_text(mode)
    task = create(runtime)
    runtime.executor.execute(task["id"], "1")
    state = detail(runtime, task)
    assert state["status"] == "failed" and state["errorCode"] == "canvas_depth_failed", state
    assert state["canResume"] and "resultJson" not in state
    runtime.executor.execute(task["id"], "1")
    assert len(calls(runtime)) == 1
    with runtime.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 1
    clean(runtime)


def test_ready_archive_recovers_same_task_without_second_inference(depth_runtime, monkeypatch):
    runtime = depth_runtime
    task = create(runtime)
    original = runtime.executor._heartbeat

    def lose_receipt(identifier, token, stage, progress):
        if stage == "检查深度视频…" and progress == 99:
            raise RuntimeError("controlled completion receipt failure")
        original(identifier, token, stage, progress)

    monkeypatch.setattr(runtime.executor, "_heartbeat", lose_receipt)
    runtime.executor.execute(task["id"], "1")
    assert detail(runtime, task)["status"] == "failed"
    with runtime.app[1]() as session:
        media_id = session.scalar(
            select(CanvasResourceUpload.media_id).where(
                CanvasResourceUpload.idempotency_hash
                == hashlib.sha256(archive_key(int(task["id"])).encode()).hexdigest()
            )
        )
        assert (
            media_id and session.get(CanvasLocalJob, int(task["id"])).result_json["device"] == "cpu"
        )
    monkeypatch.setattr(runtime.executor, "_heartbeat", original)
    resumed = runtime.owner.post(PATH + f"/tasks/{task['id']}/resume")
    assert resumed.status_code == 200 and resumed.json()["id"] == task["id"]
    runtime.executor.execute(task["id"], "1")
    runtime.executor.execute(task["id"], "2")
    state = detail(runtime, task)
    assert state["status"] == "succeeded", state
    assert json.loads(state["resultJson"])["resourceId"] == str(media_id)
    assert len(calls(runtime)) == 1
    clean(runtime)


def test_cancel_stops_owned_component_and_cleans_output(depth_runtime):
    runtime = depth_runtime
    (runtime.component / "mode").write_text("hold")
    task = create(runtime)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.executor.execute, task["id"], "1")
        deadline = time.monotonic() + 20
        while not (runtime.component / "entered").exists():
            assert time.monotonic() < deadline, detail(runtime, task)
            time.sleep(0.05)
        cancel = runtime.owner.post(PATH + f"/tasks/{task['id']}/cancel")
        assert cancel.status_code == 200, cancel.text
        future.result(timeout=15)
    state = detail(runtime, task)
    assert state["status"] == "cancelled" and "resultJson" not in state, state
    with runtime.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 1
    clean(runtime)


def test_frozen_checksum_mismatch_never_enters_component(depth_runtime):
    runtime = depth_runtime
    task = create(runtime)
    with runtime.app[1].begin() as session:
        job = session.get(CanvasLocalJob, int(task["id"]))
        job.source_snapshot = {**job.source_snapshot, "checksum": "b" * 64}
    runtime.executor.execute(task["id"], "1")
    state = detail(runtime, task)
    assert state["status"] == "failed" and "字节校验" in state["error"], state
    assert calls(runtime) == []
    clean(runtime)


def test_global_private_depth_output_keeps_global_author_scope(depth_runtime):
    runtime = depth_runtime
    with runtime.app[1].begin() as session:
        source = session.get(MediaFile, int(runtime.source["id"]))
        source.project_id, source.scope_user_id = None, int(runtime.user["id"])
        upload = session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.media_id == source.id)
        )
        upload.project_id = upload.canvas_id = None
        upload.scope_user_id = int(runtime.user["id"])
    runtime.body.pop("projectId")
    task = create(runtime)
    runtime.executor.execute(task["id"], "1")
    state = detail(runtime, task)
    assert state["status"] == "succeeded" and "projectId" not in state, state
    result = json.loads(state["resultJson"])
    with runtime.app[1]() as session:
        media = session.get(MediaFile, int(result["resourceId"]))
        assert media.project_id is None and media.scope_user_id == int(runtime.user["id"])
    other, _ = account(runtime.app, "depth_global_other")
    assert other.get(PATH + f"/resources/{result['resourceId']}/file").status_code == 404
    clean(runtime)
