"""真实媒体归档与本人任务列表并发时保持父级、任务锁顺序。"""

import json
import os
import threading

import pytest
from sqlalchemy import text

from short_drama.dao.task_runtime_dao import owned_task
from short_drama.service.canvas_generation_service import CanvasGenerationService
from short_drama.service.generation_execution_service import GenerationExecutionService
from tests.integration.test_canvas_generation_runtime import TASKS, admit
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_runninghub_declarative import (
    declarative_runtime as declarative_runtime,
)
from tests.integration.test_canvas_runninghub_declarative import declarative_seed
from tests.integration.test_canvas_video_protocols import execute
from tests.integration.test_canvas_workspace import actor
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable isolated real HTTP/MySQL/MinIO archive concurrency verification",
    ),
]


@pytest.mark.parametrize("scope", ["canvas", "all"])
def test_original_task_list_and_video_archive_do_not_invert_parent_locks(
    declarative_runtime, database_errors, monkeypatch, scope
):
    runtime = declarative_runtime
    client, user, _, _, request = declarative_seed(runtime)
    task = admit(client, request)
    _, actions = execute(runtime, task, steps=2)
    assert actions == ["submit", "poll"]
    executor = GenerationExecutionService(
        runtime.app[1], runtime.app[2], runtime.gateway, runtime.app[0].state.storage
    )
    archive_has_task, archive_allowed = threading.Event(), threading.Event()
    reader_started = threading.Event()
    errors, values, reader_connection = [], [], []
    ownership_calls = 0
    save_one = executor.archive.save_one

    def hold_before_media_insert(session, *args):
        nonlocal ownership_calls
        current = owned_task(session, *args)
        ownership_calls += 1
        if ownership_calls == 2:
            archive_has_task.set()
            assert archive_allowed.wait(15), "The list did not reach a real MySQL lock wait"
        return current

    def archive_with_gate(*args):
        return save_one(*args, ownership_check=hold_before_media_insert)

    monkeypatch.setattr(executor.archive, "save_one", archive_with_gate)

    def save_media():
        try:
            executor.execute(task["id"], 3)
        except BaseException as error:
            errors.append(error)

    def read_list():
        try:
            engine = runtime.app[1].kw["bind"]
            with engine.connect() as connection:
                reader_connection.append(connection.scalar(text("SELECT CONNECTION_ID()")))
                connection.rollback()
                with runtime.app[1](bind=connection) as session:
                    session.info["actor"] = actor(int(user["id"]))
                    reader_started.set()
                    values.append(
                        CanvasGenerationService(session, runtime.app[2]).list(
                            project_id=request["projectId"] if scope == "canvas" else None
                        )
                    )
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=save_media, name="canvas-media-archive-worker", daemon=True)
    reader = threading.Thread(target=read_list, name="canvas-media-list-reader", daemon=True)
    worker.start()
    try:
        assert archive_has_task.wait(10), errors
        reader.start()
        assert reader_started.wait(5), errors
        waiting_on = None
        for _ in range(150):
            with runtime.app[1]() as session:
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
        assert waiting_on in {"projects", "async_tasks"}, (errors, waiting_on)
        archive_allowed.set()
        worker.join(15)
        reader.join(15)
        assert not worker.is_alive() and not reader.is_alive(), "Task-owned threads did not stop"
        assert errors == [] and database_errors == [], (errors, database_errors, waiting_on)
        assert len(values) == 1 and [item["id"] for item in values[0]] == [task["id"]]
        completed = client.get(f"{TASKS}/{task['id']}").json()
        assert completed["status"] == "succeeded" and completed["resultState"] == "READY"
        media = json.loads(completed["resultJson"])["video"]
        assert client.get(media["url"]).content == runtime.media
    finally:
        archive_allowed.set()
        worker.join(15)
        if reader.ident is not None:
            reader.join(15)
        assert not worker.is_alive() and not reader.is_alive()
