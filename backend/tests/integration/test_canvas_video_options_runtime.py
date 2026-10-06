"""两个真实视频准入入口的可信默认值、源 alias 覆写与幂等冻结验收。"""

import json
import os
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from short_drama.ai.canvas_video_adapters import NEWAPI_VIDEO_GENERATIONS
from short_drama.domain import AIGenerationRecord, AsyncTask
from tests.integration.test_canvas_generation_runtime import TASKS, records
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_video_protocols import (
    HEADER_NAME,
    HEADER_VALUE,
    KEY,
    WORKSPACE,
    execute,
    protocol_seed,
    submissions,
)
from tests.integration.test_canvas_video_protocols import video_runtime as video_runtime
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated video options HTTP/MySQL/MinIO verification",
    ),
]
MODEL_TESTS = "/api/v1/canvas-runtime/model-tests"
PROFILE = {
    "references": {"minImages": 0, "maxImages": 9, "maxVideos": 3, "maxAudios": 3},
    "duration": {"selection": "range", "min": 4, "max": 12, "step": 2, "default": 8},
    "ratios": ["16:9", "9:16"],
    "defaultRatio": "16:9",
    "resolutions": ["720p", "1080p"],
    "defaultResolution": "720p",
    "generateAudio": {"supported": True, "default": True},
    "watermark": {"supported": True, "default": False},
    "operations": ["text_to_video", "image_to_video", "reference_to_video"],
    "defaultOperation": "text_to_video",
}
DEFAULTS = {
    "size": "16:9",
    "videoSeconds": "8",
    "vquality": "720p",
    "videoGenerateAudio": "true",
    "videoWatermark": "false",
}
EXPLICIT = {
    "size": "9:16",
    "videoSeconds": "10",
    "vquality": "1080p",
    "videoGenerateAudio": "false",
    "videoWatermark": "true",
}


def option_request(runtime, entry):
    client, user, _, path, request = protocol_seed(runtime, NEWAPI_VIDEO_GENERATIONS)
    current = client.get(WORKSPACE).json()
    channel = next(item for item in current["channels"] if item["id"] == "protocol-provider")
    channel["modelProfiles"][0]["capabilityConfig"] = {
        "version": 1,
        "video": deepcopy(PROFILE),
    }
    response = client.put(
        WORKSPACE,
        json={
            "expected_row_version": current["row_version"],
            "preferences": current["preferences"],
            "channels": current["channels"],
        },
    )
    assert response.status_code == 200, response.text
    channel = next(item for item in response.json()["channels"] if item["id"] == channel["id"])
    if entry == "canvas":
        return client, user, path, TASKS, request
    return (
        client,
        user,
        path,
        MODEL_TESTS,
        {
            "channel": channel,
            "mode": "video",
            "model": request["model"],
            "prompt": request["prompt"],
            "config": request["input"]["config"],
            "textOptions": {"stream": False, "thinking": False},
            "clientOperationId": uuid4().hex,
        },
    )


def create(client, endpoint, body):
    headers = {"Idempotency-Key": body["clientOperationId"]} if endpoint == MODEL_TESTS else {}
    return client.post(endpoint, json=body, headers=headers)


def set_config(body, entry, config):
    if entry == "canvas":
        body["input"]["config"] = config
    else:
        body["config"] = config


def assert_canonical(runtime, client, task, expected):
    response = client.get(f"{TASKS}/{task['id']}")
    assert response.status_code == 200, response.text
    public_input = json.loads(response.json()["inputJson"])
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        frozen_input = deepcopy(record.request_data["canvas_request"]["input"])
        assert record.config_snapshot["canvas_video_capability"] == PROFILE
    for field, value in expected.items():
        assert public_input["config"][field] == frozen_input["config"][field] == value
    canonical_options = {
        **expected,
        "videoSeconds": int(expected["videoSeconds"]),
        "videoGenerateAudio": expected["videoGenerateAudio"] == "true",
        "videoWatermark": expected["videoWatermark"] == "true",
    }
    assert public_input["capabilityOptions"] == canonical_options
    assert frozen_input["capabilityOptions"] == canonical_options


def assert_wire(runtime, expected):
    actual = submissions(runtime)
    assert len(actual) == 1
    wire = actual[0]["body"]
    assert wire["seconds"] == expected["videoSeconds"]
    assert wire["aspect_ratio"] == expected["size"]
    assert wire["resolution"] == expected["vquality"]
    assert wire["generate_audio"] is (expected["videoGenerateAudio"] == "true")


@pytest.mark.parametrize("entry", ["canvas", "model-test"])
@pytest.mark.parametrize("variant", ["blank-defaults", "explicit-options"])
def test_trusted_video_options_are_identical_in_public_input_freeze_and_wire(
    video_runtime, entry, variant
):
    runtime = video_runtime
    client, user, path, endpoint, body = option_request(runtime, entry)
    if variant == "blank-defaults":
        set_config(body, entry, {field: " \t " for field in DEFAULTS})
        expected = DEFAULTS
    else:
        set_config(body, entry, {**EXPLICIT, "vquality": "1080"})
        expected = EXPLICIT
        if entry == "canvas":
            set_config(body, entry, DEFAULTS.copy())
            body["input"]["capabilityOptions"] = {
                "duration": 10,
                "aspectRatio": "9:16",
                "resolution": "1080",
                "videoGenerateAudio": False,
                "videoWatermark": True,
            }
    original = deepcopy(body)
    before = client.get(path + "/my-document").json()
    admitted = create(client, endpoint, body)
    assert admitted.status_code == 202, admitted.text
    task = admitted.json()
    assert body == original
    assert_canonical(runtime, client, task, expected)
    replay = create(client, endpoint, original)
    assert replay.status_code == 200 and replay.json()["id"] == task["id"]
    execute(runtime, task)
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert_canonical(runtime, client, task, expected)
    assert_wire(runtime, expected)
    if entry == "canvas":
        assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
    else:
        assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 0}
        assert client.get(path + "/my-document").json() == before


@pytest.mark.parametrize("entry", ["canvas", "model-test"])
@pytest.mark.parametrize("invalid", ["duration-step", "resolution", "profile"])
def test_invalid_video_options_never_admit_or_contact_supplier(video_runtime, entry, invalid):
    runtime = video_runtime
    client, user, path, endpoint, body = option_request(runtime, entry)
    config = DEFAULTS.copy()
    if invalid == "duration-step":
        config["videoSeconds"] = "5"
    elif invalid == "resolution":
        config["vquality"] = "2160p"
    elif entry == "model-test":
        body["channel"].update(id="invalid-options-draft", apiKey=KEY, credentialRef="")
        body["channel"]["headers"] = [{"name": HEADER_NAME, "value": HEADER_VALUE}]
        body["channel"]["modelProfiles"][0]["capabilityConfig"]["video"]["duration"]["step"] = 0
    else:
        config["notAProviderOption"] = "true"
    set_config(body, entry, config)
    before = client.get(path + "/my-document").json()
    response = create(client, endpoint, body)
    unknown_config = invalid == "profile" and entry == "canvas"
    assert response.status_code == (422 if unknown_config else 400), response.text
    assert response.json()["error"]["code"] == (
        "canvas_generation_option_unsupported"
        if unknown_config
        else "generation_unsupported_parameters"
    )
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert client.get(path + "/my-document").json() == before
    assert submissions(runtime) == []
    with runtime.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_capability_option_outside_source_whitelist_is_atomic_preaccept_failure(video_runtime):
    runtime = video_runtime
    client, user, path, endpoint, body = option_request(runtime, "canvas")
    set_config(body, "canvas", DEFAULTS.copy())
    body["input"]["capabilityOptions"] = {"variants": 2}
    before = client.get(path + "/my-document").json()
    response = create(client, endpoint, body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "generation_unsupported_parameters"
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert client.get(path + "/my-document").json() == before
    assert submissions(runtime) == []
