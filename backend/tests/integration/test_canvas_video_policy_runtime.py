"""固定源视频周期、有限下载和持久化计数经真实 HTTP 验收。"""

import json
import os
import time
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.ai.canvas_video_adapters import OPENAI_VIDEOS
from short_drama.dao.task_runtime_dao import finish
from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.service.base import utcnow
from short_drama.service.canvas_video_policy import POLL_STATE_KEY
from tests.integration.test_canvas_generation_runtime import TASKS, admit, records
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_video_protocols import (
    PROVIDER_ID,
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
        reason="Enable isolated video policy HTTP/MySQL/MinIO verification",
    ),
]


def poll_record(runtime, task):
    with runtime.app[1]() as session:
        row = session.get(AsyncTask, int(task["id"]))
        record = session.scalar(select(AIGenerationRecord))
        state = (record.response_data or {}).get(POLL_STATE_KEY)
        delay = (row.next_run_at - row.updated_at).total_seconds() if row.next_run_at else None
        return row.status, row.next_action, state, delay


def poll_calls(runtime):
    return [
        item
        for item in runtime.calls
        if item["method"] == "GET" and item["path"] == "/v1/videos/" + PROVIDER_ID
    ]


@pytest.mark.parametrize("category", ["not_found", "malformed"])
def test_three_consecutive_poll_failures_stop_and_counters_persist(video_runtime, category):
    runtime = video_runtime
    response = {"http_status": 404} if category == "not_found" else "malformed"
    runtime.state["poll_sequence"] = [response, response, response, "completed"]
    client, _, _, path, request = protocol_seed(runtime, OPENAI_VIDEOS)
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    execute(runtime, task, steps=1)
    for expected in range(1, 4):
        execute(runtime, task, steps=1)
        status, action, state, delay = poll_record(runtime, task)
        assert state[category] == expected
        assert state["malformed" if category == "not_found" else "not_found"] == 0
        if expected < 3:
            assert status == "running" and action == "poll"
            assert 29.9 <= delay <= 30.2
        else:
            assert status == "failed" and action is None and delay is None
    execute(runtime, task)
    assert len(poll_calls(runtime)) == 3 and len(submissions(runtime)) == 1
    assert runtime.state["poll_sequence"] == ["completed"]
    assert client.get(path + "/my-document").json() == before


def test_valid_poll_resets_both_failure_counters_before_recovery(video_runtime):
    runtime = video_runtime
    runtime.state["poll_sequence"] = [
        {"http_status": 404},
        "malformed",
        "running",
        {"http_status": 404},
        {"http_status": 404},
        "completed",
    ]
    client, _, _, _, request = protocol_seed(runtime, OPENAI_VIDEOS)
    task = admit(client, request)
    execute(runtime, task, steps=1)
    expected_states = [
        {"not_found": 1, "malformed": 0},
        {"not_found": 0, "malformed": 1},
        {"not_found": 0, "malformed": 0},
        {"not_found": 1, "malformed": 0},
        {"not_found": 2, "malformed": 0},
    ]
    for expected in expected_states:
        execute(runtime, task, steps=1)
        status, action, state, delay = poll_record(runtime, task)
        assert status == "running" and action == "poll" and state == expected
        assert 29.9 <= delay <= 30.2
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
    media = json.loads(completed["resultJson"])["video"]
    assert client.get(media["url"]).content == runtime.media
    assert len(poll_calls(runtime)) == 6 and len(submissions(runtime)) == 1
    assert poll_record(runtime, task)[2] == {"not_found": 0, "malformed": 0}


@pytest.mark.parametrize("retry_after,expected_delay", [("1", 30), ("45", 45)])
def test_retry_after_cannot_shorten_source_thirty_second_poll(
    video_runtime, retry_after, expected_delay
):
    runtime = video_runtime
    runtime.state["poll_sequence"] = [
        {"http_status": 429, "headers": {"Retry-After": retry_after}},
        "completed",
    ]
    client, _, _, _, request = protocol_seed(runtime, OPENAI_VIDEOS)
    task = admit(client, request)
    execute(runtime, task, steps=2)
    status, action, state, delay = poll_record(runtime, task)
    assert status == "running" and action == "poll"
    assert state == {"not_found": 0, "malformed": 0}
    assert expected_delay - 0.1 <= delay <= expected_delay + 0.2
    execute(runtime, task)
    assert client.get(f"{TASKS}/{task['id']}").json()["status"] == "succeeded"
    assert len(poll_calls(runtime)) == 2 and len(submissions(runtime)) == 1


def test_expired_budget_before_poll_sends_no_provider_get(video_runtime):
    runtime = video_runtime
    client, _, _, _, request = protocol_seed(runtime, OPENAI_VIDEOS)
    task = admit(client, request)
    execute(runtime, task, steps=1)
    with runtime.app[1].begin() as session:
        record = session.scalar(select(AIGenerationRecord))
        row = session.get(AsyncTask, int(task["id"]))
        assert row.next_action == "poll" and record.provider_task_id == PROVIDER_ID
        row.started_at = utcnow() - timedelta(seconds=record.config_snapshot["budget_seconds"] + 1)
        # 构造已运行到预算之外的旧任务，同时满足真实 MySQL 时间顺序约束。
        row.created_at = row.started_at - timedelta(seconds=1)
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "failed" and completed["errorCode"] == "generation_timeout"
    assert poll_calls(runtime) == [] and len(submissions(runtime)) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_poll_authentication_failure_stops_after_one_http_get(video_runtime, status):
    runtime = video_runtime
    runtime.state["poll_sequence"] = [{"http_status": status}, "completed"]
    client, _, _, _, request = protocol_seed(runtime, OPENAI_VIDEOS)
    task = admit(client, request)
    execute(runtime, task)
    completed = client.get(f"{TASKS}/{task['id']}").json()
    assert completed["status"] == "failed"
    assert len(poll_calls(runtime)) == 1 and len(submissions(runtime)) == 1
    assert runtime.state["poll_sequence"] == ["completed"]


def content_calls(runtime):
    return [
        item
        for item in runtime.calls
        if item["method"] == "GET" and item["path"] == "/v1/videos/" + PROVIDER_ID + "/content"
    ]


@pytest.mark.parametrize(
    "sequence,expected",
    [([503, 503, None], "succeeded"), ([503, 503, 503, None], "failed"), ([401, None], "failed")],
    ids=["download-recovers", "download-exhausted", "download-auth-failure"],
)
def test_real_content_download_uses_bounded_save_schedule_without_duplicate_create(
    video_runtime, sequence, expected
):
    runtime = video_runtime
    runtime.state["download_sequence"] = sequence.copy()
    client, user, _, path, request = protocol_seed(runtime, OPENAI_VIDEOS)
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    execute(runtime, task, steps=2)
    expected_attempts = 1 if sequence[0] == 401 else 3
    for attempt in range(expected_attempts):
        execute(runtime, task, steps=1)
        status, action, _, delay = poll_record(runtime, task)
        if attempt < expected_attempts - 1:
            assert status == "running" and action == "save"
            assert 29.9 <= delay <= 30.2
        else:
            assert status == expected and action is None and delay is None
    response = client.get(f"{TASKS}/{task['id']}")
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["status"] == expected
    assert len(content_calls(runtime)) == expected_attempts
    assert len(submissions(runtime)) == len(poll_calls(runtime)) == 1
    if expected == "succeeded":
        media = json.loads(current["resultJson"])["video"]
        assert client.get(media["url"]).content == runtime.media
    else:
        assert current["outputs"] == []
        execute(runtime, task)
        assert len(content_calls(runtime)) == expected_attempts
        if sequence[0] == 503:
            assert current["errorCode"] == "download_failed", current
            assert current["canRetry"] is False and current["canResume"] is True
            retry = deepcopy(request)
            retry["input"]["metadata"].update(clientOperationId=uuid4().hex, retryOf=task["id"])
            rejected = client.post(TASKS, json=retry)
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["error"]["code"] == "canvas_task_retry_not_allowed"
            assert records(runtime.app, user) == {"tasks": 1, "records": 1, "bindings": 1}
            assert client.get(path + "/my-document").json() == before
            assert len(submissions(runtime)) == len(poll_calls(runtime)) == 1
            assert len(content_calls(runtime)) == expected_attempts


def test_original_query_retries_actual_transient_content_three_times_without_create(video_runtime):
    runtime = video_runtime
    runtime.state["download_sequence"] = [503, 503, None]
    client, _, _, _, request = protocol_seed(runtime, OPENAI_VIDEOS)
    task = admit(client, request)
    with runtime.app[1].begin() as session:
        row = session.get(AsyncTask, int(task["id"]))
        call = session.scalar(select(AIGenerationRecord))
        finish(row, "failed", {"code": "poll_failed", "message": "受控原查询失败"})
        call.provider_task_id, call.status = PROVIDER_ID, "failed"
        call.finished_at = call.updated_at = utcnow()
        call.error = {"code": "poll_failed", "message": "受控原查询失败"}
    started = time.monotonic()
    response = client.post(f"{TASKS}/{task['id']}/query-provider")
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recovered"] is True and result["task"]["id"] == task["id"]
    assert result["task"]["status"] == "succeeded"
    downloaded = content_calls(runtime)
    assert len(downloaded) == 3 and submissions(runtime) == [] and len(poll_calls(runtime)) == 1
    # 同步原任务 query 真正等待两次生产 30 秒周期，没有替换 Gateway 或等待函数。
    assert elapsed >= 60
    assert all(
        right["observed_at"] - left["observed_at"] >= 30
        for left, right in zip(downloaded[:-1], downloaded[1:], strict=True)
    )
    media = json.loads(result["task"]["resultJson"])["video"]
    assert client.get(media["url"]).content == runtime.media
    before = len(runtime.calls)
    assert client.post(f"{TASKS}/{task['id']}/query-provider").json() == result
    assert len(runtime.calls) == before
