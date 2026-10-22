"""受控Chat/Responses经真实FFmpeg视频、HTTP、MySQL与MinIO验证文字视频引用。"""

import hashlib
import json
import os
import subprocess
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, CanvasTaskMediaReference, MediaFile
from short_drama.service.video_render import executable
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, admit, records
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    CAPABILITY,
    FIRST,
    SECOND,
    assert_wire,
    executor,
    first_delta,
    running,
    text_seed,
)
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    text_image_runtime as text_image_runtime,
)
from tests.integration.canvas.resources.test_canvas_resources import canvas, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated text video HTTP/MySQL/MinIO verification",
    ),
]


def video_bytes(tmp_path, color):
    target = tmp_path / (color + ".mp4")
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color={color}:size=160x90:rate=24",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return target.read_bytes()


def saved_video(client, source, content):
    response = upload(
        client,
        content,
        source=source,
        key=uuid4().hex,
        kind="video",
        name="reference.mp4",
        mime="video/mp4",
    )
    assert response.status_code == 200, response.text
    resource = response.json()["resource"]
    assert (resource["width"], resource["height"], resource["durationMs"]) == (160, 90, 1000)
    return {
        "id": "video-" + uuid4().hex,
        "storageKey": "resource:" + resource["id"],
        "type": "image/png",
        "bytes": 1,
        "width": 1,
        "height": 1,
        "durationMs": 1,
        "url": "https://never-fetch.example/forged.mp4",
    }


def video_seed(runtime, tmp_path, *, capability=None, mixed=False):
    client, user, project, path, request, images = text_seed(
        runtime, capability=capability, images=mixed
    )
    contents = [video_bytes(tmp_path, color) for color in ("red", "blue")]
    request["input"]["referenceVideos"] = [
        saved_video(client, request["projectId"], content) for content in contents
    ]
    return client, user, project, path, request, images, contents


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("stream", [False, True])
def test_saved_text_videos_freeze_real_metadata_order_and_private_stream(
    text_image_runtime, tmp_path, mixed, stream
):
    runtime = text_image_runtime
    client, user, project, path, request, images, contents = video_seed(
        runtime, tmp_path, mixed=mixed
    )
    member, other = account(runtime.app, "canvas_text_video_reader")
    request["input"]["textOptions"]["stream"] = stream
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    changed = deepcopy(request)
    changed["input"]["referenceVideos"].reverse()
    assert client.post(TASKS, json=changed).status_code == 409
    if stream:
        runtime.state["hold"] = True
        with running(runtime, task):
            assert first_delta(client, task)["textDraft"] == FIRST
            assert member.get(f"{TASKS}/{task['id']}/text-deltas").status_code == 404
            assert client.get(path + "/my-document").json() == before
    else:
        executor(runtime).execute(task["id"], 1)
    result = client.get(f"{TASKS}/{task['id']}").json()
    assert result["status"] == "succeeded", result
    assert json.loads(result["resultJson"])["text"] == FIRST + SECOND
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    executor(runtime).execute(task["id"], 1)
    assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    assert client.get(path + "/my-document").json() == before
    assert_wire(runtime, request, images, stream, videos=contents)
    public = json.loads(task["inputJson"])["referenceVideos"]
    identifiers = [item["storageKey"].removeprefix("resource:") for item in public]
    assert [item["bytes"] for item in public] == [len(content) for content in contents]
    assert all(
        (item["type"], item["width"], item["height"], item["durationMs"])
        == ("video/mp4", 160, 90, 1000)
        and "url" not in item
        for item in public
    )
    with runtime.app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        frozen = record.request_data
        assert frozen["canvas_text_references"]["videos"] == [
            {
                "media_id": identifier,
                "mime_type": "video/mp4",
                "byte_size": len(content),
                "duration_ms": 1000,
            }
            for identifier, content in zip(identifiers, contents, strict=True)
        ]
        assert frozen["input"]["video_reference_media_ids"] == identifiers
        assert "video_reference_urls" not in frozen["input"]
        assert "X-Amz-" not in json.dumps(frozen)
        protected = list(
            session.scalars(
                select(CanvasTaskMediaReference)
                .where(CanvasTaskMediaReference.role == "video_reference_media_ids")
                .order_by(CanvasTaskMediaReference.ordinal)
            )
        )
        assert [str(reference.media_id) for reference in protected] == identifiers
        for identifier, content in zip(identifiers, contents, strict=True):
            media = session.get(MediaFile, int(identifier))
            assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    "failure,status,code",
    [
        ("undeclared", 422, "reference_limit_exceeded"),
        ("count", 422, "reference_limit_exceeded"),
        ("bytes", 422, "reference_media_too_large"),
        ("unsaved", 422, "canvas_reference_not_saved"),
        ("other-project", 404, "not_found"),
        ("other-private", 404, "not_found"),
    ],
)
def test_text_video_rejection_leaves_no_task_supplier_call_or_partial_draft(
    text_image_runtime, tmp_path, failure, status, code
):
    runtime = text_image_runtime
    capability = deepcopy(CAPABILITY)
    if failure == "undeclared":
        capability["references"].pop("maxVideos")
    elif failure == "count":
        capability["references"]["maxVideos"] = 1
    elif failure == "bytes":
        capability["references"]["maxVideoBytes"] = 1
    client, user, project, path, request, _, contents = video_seed(
        runtime, tmp_path, capability=capability, mixed=True
    )
    if failure == "unsaved":
        request["input"]["referenceVideos"][1]["storageKey"] = "video:local-only"
    elif failure == "other-project":
        _, _, document = canvas(client, "text-video-other-project")
        request["input"]["referenceVideos"][1] = saved_video(client, document["id"], contents[1])
    elif failure == "other-private":
        member, other = account(runtime.app, "canvas_text_video_private_member")
        request["input"]["referenceVideos"][1] = saved_video(
            member, request["projectId"], contents[1]
        )
    before = client.get(path + "/my-document").json()
    response = client.post(TASKS, json=request)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert client.get(path + "/my-document").json() == before
    assert runtime.posts == []
