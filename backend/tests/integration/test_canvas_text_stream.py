"""真实 Worker 回调、MySQL 正文增量与认证 SSE；供应商受控，不伪造浏览器正文。"""

import json
import threading
from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from short_drama.ai import GenerationError, GenerationResult
from short_drama.dao.task_runtime_dao import LeaseLost
from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskTextDelta
from short_drama.service import canvas_text_stream
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_browser import isolated_api
from tests.integration.test_canvas_generation_runtime import TASKS, admit, seed
from tests.integration.test_canvas_workspace import actor
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
FIRST = "真实供应商第一段🌤️"
SECOND = "，第二段中文正文。"


@contextmanager
def running_text(identity_app, task, *, two_chunks=True):
    finish_allowed = threading.Event()
    second_allowed = threading.Event()
    chunks_persisted = threading.Event()
    first_persisted = threading.Event()
    errors = []

    class Gateway:
        calls = 0

        def validate(self, *_args):
            return {}

        def submit(self, *_args, **kwargs):
            self.calls += 1
            callback = kwargs.get("on_text_delta")
            assert callback is not None, "Source stream=true must use the internal Worker callback"
            self.callback = callback
            callback(FIRST)
            first_persisted.set()
            assert second_allowed.wait(10), "The test did not allow the second provider chunk"
            # Cross the real writer's batching interval; neither fake DB writes nor forced flush.
            threading.Event().wait(0.12)
            callback(SECOND)
            chunks_persisted.set()
            assert finish_allowed.wait(10), "The test did not allow provider completion"
            return GenerationResult("succeeded", "openai_chat.v1", text=FIRST + SECOND)

    gateway = Gateway()
    executor = GenerationExecutionService(identity_app[1], identity_app[2], gateway, None)

    def execute():
        try:
            executor.execute(task["id"], 1)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=execute, name="canvas-test-text-stream", daemon=True)
    worker.start()
    try:
        assert first_persisted.wait(5), errors
        if two_chunks:
            second_allowed.set()
            assert chunks_persisted.wait(5), errors
        yield gateway, finish_allowed, second_allowed
    finally:
        second_allowed.set()
        finish_allowed.set()
        worker.join(5)
        assert not worker.is_alive(), "Task-owned text Worker did not stop"
        assert not errors, errors


def events(content):
    parsed = []
    for block in content.replace("\r\n", "\n").split("\n\n"):
        event, identifier, data = None, None, []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("id:"):
                identifier = int(line[3:].strip())
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            parsed.append({"event": event, "id": identifier, "data": json.loads("\n".join(data))})
    return parsed


def test_worker_deltas_are_durable_private_and_replayed_by_source_sequence(identity_app):
    client, user, project, path, request = seed(identity_app, "canvas_text_stream_author")
    member, other = account(identity_app, "canvas_text_stream_reader")
    join(identity_app, client, member, project["id"], other["id"])
    member.headers["X-Canvas-Actor"] = other["id"]
    request["input"]["textOptions"]["stream"] = True
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    replay_path = f"{TASKS}/{task['id']}/text-deltas"
    stream_path = f"{TASKS}/{task['id']}/text-events"
    with running_text(identity_app, task) as (gateway, finish_allowed, _second_allowed):
        replay = client.get(replay_path, params={"after": 0})
        assert replay.status_code == 200, replay.text
        value = replay.json()
        assert value["complete"] is False and value["status"] == "running"
        assert value["textDraft"] == FIRST + SECOND
        assert not value.get("finalText")
        assert [delta["sequence"] for delta in value["deltas"]] == [1, 2]
        assert [delta["content"] for delta in value["deltas"]] == [FIRST, SECOND]
        for delta in value["deltas"]:
            assert delta["id"].isdecimal() and delta["taskId"] == task["id"]
            assert delta["byteCount"] == len(delta["content"].encode())
        detail = client.get(f"{TASKS}/{task['id']}").json()
        assert detail["textDraft"] == FIRST + SECOND and detail["textDraftSequence"] == 2
        assert detail["resultState"] == "NOT_AVAILABLE"
        assert client.get(path + "/my-document").json() == before
        assert FIRST not in json.dumps(member.get(path).json(), ensure_ascii=False)
        assert member.get(replay_path).status_code == 404
        assert member.get(stream_path).status_code == 404
        # Client-authored replay data is not accepted into the provider archive.
        assert client.post(replay_path, json={"content": "伪造供应商正文"}).status_code == 405
        assert client.get(replay_path, params={"after": -1}).status_code == 422
        with identity_app[1]() as session:
            session.info["actor"] = actor(int(user["id"]))
            saved = list(
                session.scalars(select(CanvasTaskTextDelta).order_by(CanvasTaskTextDelta.sequence))
            )
            assert [item.content for item in saved] == [FIRST, SECOND]
        with identity_app[1]() as session:
            session.info["actor"] = actor(int(other["id"]))
            assert list(session.scalars(select(CanvasTaskTextDelta))) == []
        finish_allowed.set()
    final = client.get(replay_path, params={"after": 1})
    assert final.status_code == 200, final.text
    completed = final.json()
    assert completed["complete"] is True and completed["status"] == "succeeded"
    assert completed["finalText"] == FIRST + SECOND
    assert [delta["sequence"] for delta in completed["deltas"]] == [2]
    assert client.get(path + "/my-document").json() == before
    all_response = client.get(stream_path)
    assert all_response.status_code == 200, all_response.text
    assert all_response.headers["content-type"].startswith("text/event-stream")
    full = events(all_response.text)
    deltas = [item for item in full if item["event"] == "delta"]
    assert [item["id"] for item in deltas] == [1, 2]
    assert [item["data"]["sequence"] for item in deltas] == [1, 2]
    assert "".join(item["data"]["content"] for item in deltas) == FIRST + SECOND
    terminal = [item for item in full if item["event"] == "terminal"]
    assert len(terminal) == 1 and terminal[0]["data"]["finalText"] == FIRST + SECOND
    assert any(item["event"] == "progress" for item in full)
    resumed = events(client.get(stream_path, headers={"Last-Event-ID": "1"}).text)
    assert [item["id"] for item in resumed if item["event"] == "delta"] == [2]
    empty = events(
        client.get(stream_path, params={"after": 2}, headers={"Last-Event-ID": "1"}).text
    )
    assert not [item for item in empty if item["event"] == "delta"]
    assert any(item["event"] == "terminal" for item in empty)
    assert gateway.calls == 1


def test_provider_disconnect_preserves_partial_text_without_resubmitting(identity_app):
    client, _, _, path, request = seed(identity_app, "canvas_text_stream_disconnect")
    request["input"]["textOptions"]["stream"] = True
    task = admit(client, request)
    before = client.get(path + "/my-document").json()

    class Gateway:
        calls = 0

        def validate(self, *_args):
            return {}

        def submit(self, *_args, **kwargs):
            self.calls += 1
            kwargs["on_text_delta"](FIRST)
            kwargs["on_text_delta"](SECOND)
            raise GenerationError("acceptance_unknown", accepted_unknown=True)

    gateway = Gateway()
    executor = GenerationExecutionService(identity_app[1], identity_app[2], gateway, None)
    executor.execute(task["id"], 1)
    executor.execute(task["id"], 1)
    current = client.get(f"{TASKS}/{task['id']}")
    assert current.status_code == 200, current.text
    value = current.json()
    assert value["status"] == "failed" and value["textDraft"] == FIRST + SECOND
    assert value["canRetry"] is False and value["canResume"] is False
    replay_path = f"{TASKS}/{task['id']}/text-deltas"
    partial = client.get(replay_path).json()
    assert partial["complete"] and partial["status"] == "failed"
    assert partial["textDraft"] == FIRST + SECOND and not partial.get("finalText")
    assert "".join(item["content"] for item in partial["deltas"]) == FIRST + SECOND
    assert client.post(f"{TASKS}/{task['id']}/resume").status_code == 409
    assert client.post(TASKS, json=request).json()["id"] == task["id"]
    assert client.get(path + "/my-document").json() == before
    assert gateway.calls == 1


def test_expired_writer_cannot_archive_again_after_lease_ownership_changes(identity_app):
    client, _, _, _, request = seed(identity_app, "canvas_text_stream_lease")
    request["input"]["textOptions"]["stream"] = True
    task = admit(client, request)
    with running_text(identity_app, task, two_chunks=False) as (
        gateway,
        _finish_allowed,
        _second_allowed,
    ):
        with identity_app[1].begin() as session:
            current = session.get(AsyncTask, int(task["id"]), with_for_update=True)
            original_token = current.lock_token
            current.lock_token = "replacement-worker-lease"
            current.locked_until = utcnow() + timedelta(minutes=1)
            current.message_version += 1
            assert original_token and original_token != current.lock_token
        threading.Event().wait(0.12)
        with pytest.raises(LeaseLost):
            gateway.callback("过期租约不允许写入的正文")
        with identity_app[1]() as session:
            rows = list(session.scalars(select(CanvasTaskTextDelta)))
            assert len(rows) == 1 and rows[0].content == FIRST
            record = session.scalar(select(AIGenerationRecord))
            assert record.response_data["canvas_text_draft"] == FIRST
    assert gateway.calls == 1


def test_quota_failure_rolls_back_the_whole_flush_and_preserves_the_prior_draft(
    identity_app, monkeypatch
):
    client, _, _, path, request = seed(identity_app, "canvas_text_stream_quota")
    request["input"]["textOptions"]["stream"] = True
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    # Exercise the byte boundary and transaction with a small explicit test quota.
    # This is not a stress measurement of the production 2 MB / 64 MB limits.
    monkeypatch.setattr(canvas_text_stream, "MAX_TASK_BYTES", len((FIRST + SECOND).encode()) - 1)

    class Gateway:
        calls = 0

        def validate(self, *_args):
            return {}

        def submit(self, *_args, **kwargs):
            self.calls += 1
            kwargs["on_text_delta"](FIRST)
            threading.Event().wait(0.12)
            kwargs["on_text_delta"](SECOND)
            raise AssertionError("A quota-rejected flush must stop the supplier stream")

    gateway = Gateway()
    executor = GenerationExecutionService(identity_app[1], identity_app[2], gateway, None)
    executor.execute(task["id"], 1)
    executor.execute(task["id"], 1)
    with identity_app[1]() as session:
        rows = list(session.scalars(select(CanvasTaskTextDelta)))
        assert len(rows) == 1 and rows[0].content == FIRST
        record = session.scalar(select(AIGenerationRecord))
        assert record.status == "unknown"
        assert record.error["code"] == "canvas_text_replay_quota"
        assert record.response_data["canvas_text_draft"] == FIRST
    response = client.get(f"{TASKS}/{task['id']}")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "failed" and value["textDraft"] == FIRST
    assert value["canRetry"] is False and value["canResume"] is False
    assert client.get(path + "/my-document").json() == before
    assert gateway.calls == 1


def test_an_open_sse_connection_stops_after_the_task_author_is_revoked(identity_app):
    owner, _, project, _, _ = seed(identity_app, "canvas_stream_project_owner")
    member, user = account(identity_app, "canvas_stream_revoked_author")
    join(identity_app, owner, member, project["id"], user["id"])
    member, _, _, _, request = seed(
        identity_app, "unused", client=member, user=user, project=project
    )
    request["input"]["textOptions"]["stream"] = True
    task = admit(member, request)
    stream_path = f"{TASKS}/{task['id']}/text-events"
    replay_path = f"{TASKS}/{task['id']}/text-deltas"
    first_received, stream_closed = threading.Event(), threading.Event()
    received, reader_errors = [], []
    cookies = "; ".join(f"{cookie.name}={cookie.value}" for cookie in member.cookies.jar)
    with running_text(identity_app, task, two_chunks=False) as (
        gateway,
        _finish_allowed,
        second_allowed,
    ):
        with isolated_api(identity_app[1], identity_app[2]) as api_url:

            def consume():
                try:
                    with httpx.Client(
                        headers={"Cookie": cookies, "X-Canvas-Actor": user["id"]},
                        timeout=httpx.Timeout(10, read=6),
                    ) as live:
                        with live.stream("GET", api_url + stream_path) as response:
                            assert response.status_code == 200
                            for line in response.iter_lines():
                                received.append(line)
                                if line.startswith("data:"):
                                    data = json.loads(line[5:].lstrip())
                                    if data.get("content") == FIRST:
                                        first_received.set()
                except BaseException as error:
                    reader_errors.append(error)
                finally:
                    stream_closed.set()

            reader = threading.Thread(target=consume, name="canvas-test-sse-reader", daemon=True)
            reader.start()
            try:
                assert first_received.wait(5), reader_errors
                revoked = owner.delete(f"/api/v1/projects/{project['id']}/members/{user['id']}")
                assert revoked.status_code == 200, revoked.text
                assert member.get(replay_path).status_code == 404
                assert member.get(stream_path).status_code == 404
                second_allowed.set()
                assert stream_closed.wait(5), "The already-open SSE leaked beyond revocation"
            finally:
                second_allowed.set()
                _finish_allowed.set()
                reader.join(7)
            assert not reader.is_alive(), "Task-owned SSE reader did not stop"
            assert not reader_errors, reader_errors
            delivered = events("\n".join(received))
            assert SECOND not in "".join(
                item["data"].get("content", "") for item in delivered if item["event"] == "delta"
            )
            assert any(item["event"] == "error" for item in delivered)
    assert gateway.calls == 1
