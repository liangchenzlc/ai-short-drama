"""真实 Worker 回调、MySQL 正文增量与认证 SSE；供应商受控，不伪造浏览器正文。"""

import json
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from short_drama.ai import GenerationError, GenerationResult
from short_drama.dao.canvas_generation_dao import CanvasGenerationDAO
from short_drama.dao.task_runtime_dao import LeaseLost
from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskTextDelta
from short_drama.service import canvas_text_stream
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.canvas.document.test_canvas_workspace import actor
from tests.integration.canvas.generation.test_canvas_generation_runtime import TASKS, admit, seed
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

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


@pytest.mark.parametrize("scope", ["canvas", "author"])
def test_task_list_locks_batch_parents_before_text_ranges(
    identity_app, database_errors, monkeypatch, scope
):
    from short_drama.service.canvas_generation_service import CanvasGenerationService

    client, user, _, _, request = seed(identity_app, "canvas_text_batch_lock_order")
    request["input"]["textOptions"]["stream"] = True
    first_task = admit(client, request)
    second_request = deepcopy(request)
    second_request["input"]["metadata"]["clientOperationId"] += "-second"
    second_task = admit(client, second_request)
    writer_has_task = threading.Event()
    writer_allowed = threading.Event()
    first_persisted = threading.Event()
    finish_allowed = threading.Event()
    reader_started = threading.Event()
    errors, values, reader_connection, listed_ids = [], [], [], []
    original_owned_task = canvas_text_stream.owned_task
    original_task_ids = CanvasGenerationDAO.task_ids

    def hold_after_real_task_lock(session, *args):
        current = original_owned_task(session, *args)
        writer_has_task.set()
        assert writer_allowed.wait(15), "The list did not reach the second task's MySQL lock"
        return current

    def observe_list_order(dao, *args, **kwargs):
        identifiers = original_task_ids(dao, *args, **kwargs)
        listed_ids.extend(identifiers)
        return identifiers

    monkeypatch.setattr(canvas_text_stream, "owned_task", hold_after_real_task_lock)
    monkeypatch.setattr(CanvasGenerationDAO, "task_ids", observe_list_order)

    class Gateway:
        def validate(self, *_args):
            return {}

        def submit(self, *_args, **kwargs):
            kwargs["on_text_delta"](FIRST)
            first_persisted.set()
            assert finish_allowed.wait(15), "The list did not release provider completion"
            return GenerationResult("succeeded", "openai_chat.v1", text=FIRST)

    executor = GenerationExecutionService(identity_app[1], identity_app[2], Gateway(), None)

    def execute():
        try:
            executor.execute(second_task["id"], 1)
        except BaseException as error:
            errors.append(error)

    def read_list():
        try:
            engine = identity_app[1].kw["bind"]
            with engine.connect() as connection:
                reader_connection.append(connection.scalar(text("SELECT CONNECTION_ID()")))
                connection.rollback()
                with identity_app[1](bind=connection) as session:
                    session.info["actor"] = actor(int(user["id"]))
                    reader_started.set()
                    values.extend(
                        CanvasGenerationService(session, identity_app[2]).list(
                            project_id=request["projectId"] if scope == "canvas" else None,
                            active_only=True,
                        )
                    )
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=execute, name="canvas-text-batch-lock-worker", daemon=True)
    reader = threading.Thread(target=read_list, name="canvas-text-batch-lock-reader", daemon=True)
    worker.start()
    try:
        assert writer_has_task.wait(5), errors
        with identity_app[1].begin() as session:
            first = session.get(AsyncTask, int(first_task["id"]), with_for_update=True)
            first.updated_at = utcnow()
        reader.start()
        assert reader_started.wait(5), errors
        waiting_on = None
        for _ in range(150):
            with identity_app[1]() as session:
                waiting_on = session.scalar(
                    text(
                        "SELECT requested.OBJECT_NAME FROM performance_schema.data_lock_waits w "
                        "JOIN performance_schema.data_locks requested "
                        "ON requested.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID "
                        "JOIN performance_schema.threads t "
                        "ON t.THREAD_ID = requested.THREAD_ID "
                        "WHERE t.PROCESSLIST_ID = :connection LIMIT 1"
                    ),
                    {"connection": reader_connection[0]},
                )
            if waiting_on:
                break
            threading.Event().wait(0.02)
        assert listed_ids == [int(first_task["id"]), int(second_task["id"])], listed_ids
        assert waiting_on == "async_tasks", (errors, waiting_on)
        # 旧列表此时已锁住第一项的空正文范围，再等待第二项；真实插入形成死锁。
        writer_allowed.set()
        reader.join(10)
        assert not reader.is_alive(), "Task-owned batch list reader did not stop"
        assert first_persisted.wait(5), errors
        assert errors == [] and database_errors == [], (errors, database_errors)
        second = next(value for value in values if value["id"] == second_task["id"])
        assert second["textDraft"] == FIRST and second["textDraftSequence"] == 1
    finally:
        writer_allowed.set()
        finish_allowed.set()
        worker.join(10)
        if reader.ident is not None:
            reader.join(10)
        assert not worker.is_alive() and not reader.is_alive()


def test_text_writer_and_detail_reader_use_parent_before_task_lock_order(
    identity_app, database_errors, monkeypatch
):
    client, user, _, _, request = seed(identity_app, "canvas_text_lock_order")
    request["input"]["textOptions"]["stream"] = True
    task = admit(client, request)
    writer_has_task = threading.Event()
    writer_allowed = threading.Event()
    first_persisted = threading.Event()
    finish_allowed = threading.Event()
    reader_started = threading.Event()
    errors, values, reader_connection = [], [], []
    original_owned_task = canvas_text_stream.owned_task

    def hold_after_real_task_lock(session, *args):
        current = original_owned_task(session, *args)
        writer_has_task.set()
        assert writer_allowed.wait(10), "The reader did not reach a real MySQL lock wait"
        return current

    monkeypatch.setattr(canvas_text_stream, "owned_task", hold_after_real_task_lock)

    class Gateway:
        def validate(self, *_args):
            return {}

        def submit(self, *_args, **kwargs):
            kwargs["on_text_delta"](FIRST)
            first_persisted.set()
            assert finish_allowed.wait(10), "The test did not release provider completion"
            return GenerationResult("succeeded", "openai_chat.v1", text=FIRST)

    executor = GenerationExecutionService(identity_app[1], identity_app[2], Gateway(), None)

    def execute():
        try:
            executor.execute(task["id"], 1)
        except BaseException as error:
            errors.append(error)

    def read_detail():
        from short_drama.service.canvas_generation_service import CanvasGenerationService

        try:
            engine = identity_app[1].kw["bind"]
            with engine.connect() as connection:
                reader_connection.append(connection.scalar(text("SELECT CONNECTION_ID()")))
                connection.rollback()
                with identity_app[1](bind=connection) as session:
                    session.info["actor"] = actor(int(user["id"]))
                    reader_started.set()
                    values.append(
                        CanvasGenerationService(session, identity_app[2]).detail(int(task["id"]))
                    )
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=execute, name="canvas-text-parent-lock-worker", daemon=True)
    reader = threading.Thread(
        target=read_detail, name="canvas-text-parent-lock-reader", daemon=True
    )
    worker.start()
    try:
        assert writer_has_task.wait(5), errors
        reader.start()
        assert reader_started.wait(5), errors
        waiting_on = None
        # 用 MySQL 的实际等待状态释放闸门，不通过睡眠猜测读取是否已持锁。
        for _ in range(150):
            with identity_app[1]() as session:
                waiting_on = session.scalar(
                    text(
                        "SELECT requested.OBJECT_NAME FROM performance_schema.data_lock_waits w "
                        "JOIN performance_schema.data_locks requested "
                        "ON requested.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID "
                        "JOIN performance_schema.threads t "
                        "ON t.THREAD_ID = requested.THREAD_ID "
                        "WHERE t.PROCESSLIST_ID = :connection LIMIT 1"
                    ),
                    {"connection": reader_connection[0]},
                )
            if waiting_on:
                break
            threading.Event().wait(0.02)
        assert waiting_on in {"projects", "project_canvases", "async_tasks"}, errors
        writer_allowed.set()
        reader.join(10)
        assert not reader.is_alive(), "Task-owned detail reader did not stop"
        assert first_persisted.wait(5), errors
        assert errors == [] and database_errors == [], (errors, database_errors, waiting_on)
        assert values[0]["textDraft"] == FIRST and values[0]["textDraftSequence"] == 1
    finally:
        writer_allowed.set()
        finish_allowed.set()
        worker.join(10)
        if reader.ident is not None:
            reader.join(10)
        assert not worker.is_alive() and not reader.is_alive()


@pytest.mark.parametrize("view", ["text-deltas", "detail"])
def test_text_projection_reads_chunk_committed_after_ownership_snapshot(
    identity_app, monkeypatch, view
):
    client, _, _, path, request = seed(identity_app, "canvas_text_snapshot")
    request["input"]["textOptions"]["stream"] = True
    task = admit(client, request)
    before = client.get(path + "/my-document").json()
    provider_ready = threading.Event()
    first_allowed = threading.Event()
    first_persisted = threading.Event()
    finish_allowed = threading.Event()
    errors = []

    class Gateway:
        def validate(self, *_args):
            return {}

        def submit(self, *_args, **kwargs):
            provider_ready.set()
            assert first_allowed.wait(10), "The reader did not release the first provider chunk"
            kwargs["on_text_delta"](FIRST)
            first_persisted.set()
            assert finish_allowed.wait(10), "The reader did not release provider completion"
            return GenerationResult("succeeded", "openai_chat.v1", text=FIRST)

    executor = GenerationExecutionService(identity_app[1], identity_app[2], Gateway(), None)

    def execute():
        try:
            executor.execute(task["id"], 1)
        except BaseException as error:
            errors.append(error)

    original_binding = CanvasGenerationDAO.binding
    snapshot_observed = False

    def binding_then_commit_chunk(dao, identifier, actor_id):
        nonlocal snapshot_observed
        binding = original_binding(dao, identifier, actor_id)
        if identifier == int(task["id"]) and not snapshot_observed:
            snapshot_observed = True
            # This real consistent read predates the callback's real MySQL commit.
            # The service's following task/record reads acquire their existing locks.
            first_allowed.set()
            assert first_persisted.wait(5), errors
        return binding

    worker = threading.Thread(target=execute, name="canvas-text-snapshot-worker", daemon=True)
    worker.start()
    try:
        assert provider_ready.wait(5), errors
        monkeypatch.setattr(CanvasGenerationDAO, "binding", binding_then_commit_chunk)
        response = client.get(
            f"{TASKS}/{task['id']}" + ("/text-deltas" if view != "detail" else "")
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert snapshot_observed and first_persisted.is_set()
        assert value["status"] == "running" and value["textDraft"] == FIRST
        if view == "detail":
            assert value["textDraftSequence"] == 1
        else:
            assert value["complete"] is False and not value.get("finalText")
            assert [(delta["sequence"], delta["content"]) for delta in value["deltas"]] == [
                (1, FIRST)
            ]
        assert client.get(path + "/my-document").json() == before
    finally:
        first_allowed.set()
        finish_allowed.set()
        worker.join(10)
        assert not worker.is_alive(), "Task-owned snapshot Worker did not stop"
        assert errors == [], errors


def test_worker_deltas_are_durable_private_and_replayed_by_source_sequence(identity_app):
    client, user, project, path, request = seed(identity_app, "canvas_text_stream_author")
    member, other = account(identity_app, "canvas_text_stream_reader")
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
