"""受控音视频供应商经真实多动作执行器、FFprobe、MinIO归档及源回填。"""

import base64
import hashlib
import json
import os
import subprocess

import pytest
from sqlalchemy import select

from short_drama.ai import GenerationResult
from short_drama.domain import AsyncTask, MediaFile
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.service.video_render import executable
from tests.integration.test_canvas_generation_runtime import TASKS, admit, bind, seed
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable actual isolated audio/video generation MinIO verification",
    ),
]


def playable_fixture(tmp_path, kind):
    target = tmp_path / ("generated.mp4" if kind == "video" else "generated.mp3")
    source = (
        ["color=blue:size=160x90:rate=24", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if kind == "video"
        else ["sine=frequency=440:sample_rate=24000", "-c:a", "libmp3lame", "-ac", "1"]
    )
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            source[0],
            "-t",
            "1",
            *source[1:],
            str(target),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return target.read_bytes()


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_playable_media_executes_archives_materializes_then_publishes_on_bind(
    resource_app, tmp_path, kind
):
    client, _, project, path, request = seed(resource_app, "canvas_media_runtime_author", kind=kind)
    member, other = account(resource_app, "canvas_media_runtime_reader")
    join(resource_app, client, member, project["id"], other["id"])
    member.headers["X-Canvas-Actor"] = other["id"]
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    content = playable_fixture(tmp_path, kind)
    adapter = "ark_video.v1" if kind == "video" else "openai_speech.v1"

    class Gateway:
        submissions = 0
        polls = 0

        def validate(self, _snapshot, frozen, actual_adapter):
            assert actual_adapter == adapter
            assert frozen["canvas_request"]["input"]["mode"] == kind
            assert frozen["canvas_parameters"]["mode"] == kind
            if kind == "audio":
                assert frozen["canvas_parameters"]["format"] == "mp3"
            return {}

        def submit(self, *_args, **_kwargs):
            self.submissions += 1
            if kind == "video":
                return GenerationResult("submitted", adapter, provider_task_id="controlled-video")
            return self.completed()

        def poll(self, _snapshot, provider_id, _credential, actual_adapter):
            self.polls += 1
            assert kind == "video" and provider_id == "controlled-video"
            assert actual_adapter == adapter
            return self.completed()

        def completed(self):
            return GenerationResult(
                "succeeded", adapter, outputs=[{"base64": base64.b64encode(content).decode()}]
            )

    gateway = Gateway()
    executor = GenerationExecutionService(
        resource_app[1], resource_app[2], gateway, resource_app[0].state.storage
    )
    version = 1
    actions = []
    for _ in range(4):
        with resource_app[1]() as session:
            current = session.get(AsyncTask, int(task["id"]))
            if current.status in {"succeeded", "failed", "cancelled"}:
                break
            version = current.message_version
            actions.append(current.next_action)
        executor.execute(task["id"], version)
        assert client.get(path + "/my-document").json() == before
    completed_response = client.get(f"{TASKS}/{task['id']}")
    assert completed_response.status_code == 200, completed_response.text
    completed = completed_response.json()
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    assert actions == (["submit", "poll", "save"] if kind == "video" else ["submit", "save"])
    result = json.loads(completed["resultJson"])[kind]
    assert result["bytes"] == len(content)
    assert result["mimeType"] == ("video/mp4" if kind == "video" else "audio/mpeg")
    assert 900 <= result["durationMs"] <= 1500
    if kind == "video":
        assert (result["width"], result["height"], result["durationMs"]) == (160, 90, 1000)
    resource_url = result["url"]
    assert result["storageKey"].startswith("resource:")
    assert client.get(resource_url).content == content
    assert member.get(resource_url).status_code == 404
    asset_id = completed["outputs"][0]["materializedAssetId"]
    asset_path = "/api/v1/canvas-runtime/assets/" + asset_id
    asset_response = client.get(asset_path)
    assert asset_response.status_code == 200, asset_response.text
    asset = asset_response.json()["asset"]
    assert asset["kind"] == kind and asset["data"]["storageKey"] == result["storageKey"]
    assert asset["data"]["durationMs"] == result["durationMs"]
    if kind == "video":
        assert (asset["data"]["width"], asset["data"]["height"]) == (160, 90)
    assert member.get(asset_path).status_code == 404
    with resource_app[1]() as session:
        media = session.scalar(select(MediaFile))
        assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
        assert media.storage_locator.startswith("minio://canvas-test-")
    bound = bind(client, request, task)
    assert bound["result"]["node"]["metadata"]["storageKey"] == result["storageKey"]
    assert member.get(resource_url).status_code == 200
    assert member.get(resource_url).content == content
    shared = member.get(path).json()["source_document"]["nodes"][0]["metadata"]
    assert shared["storageKey"] == result["storageKey"]
    assert "taskId" not in shared and "assetId" not in shared
    assert member.get(asset_path).status_code == 404
    executor.execute(task["id"], 1)
    executor.execute(task["id"], version)
    assert gateway.submissions == 1 and gateway.polls == (1 if kind == "video" else 0)
