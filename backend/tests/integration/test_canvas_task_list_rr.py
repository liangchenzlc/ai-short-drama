"""正常真实MySQL/RR任务列表竞争；完成记录为数据库夹具，零模型或对象I/O。"""

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import Event

import pytest
from sqlalchemy import event, select, text

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasTaskBinding,
    Project,
    ProjectCanvas,
)
from short_drama.domain.collaboration import ProjectMember
from short_drama.schemas.canvas import CanvasDocument
from short_drama.service.base import utcnow
from short_drama.service.canvas_document import content_hash
from short_drama.service.canvas_generation_service import CanvasGenerationService
from short_drama.service.canvas_service import CanvasService
from short_drama.utils.snowflake import next_id
from tests.integration.test_canvas_character_rr import (
    RRCase,
    account_session,
    commit,
    document,
    graph_state,
    system_session,
    wait_for_lock,
)
from tests.integration.test_canvas_character_rr import rr_case as rr_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_CHARACTER_RR") != "1",
        reason="RUN_CANVAS_CHARACTER_RR=1 显式启用真实 MySQL RR 回归",
    ),
]


@dataclass
class TaskCase:
    first: RRCase
    second: RRCase
    task_ids: list[int]
    alternate_source: int


@pytest.fixture
def task_case(rr_case):
    first = rr_case
    second = replace(first, owning=next_id(), canvas=next_id(), key=first.key + "-second")
    alternate_source = next_id()
    now = utcnow()
    with system_session(first) as session, session.begin():
        for identifier, mode in (
            (second.owning, "infinite_canvas"),
            (alternate_source, "standard"),
        ):
            session.add(
                Project(
                    id=identifier,
                    owner_user_id=1,
                    name="任务列表RR",
                    synopsis="",
                    style="",
                    aspect="16:9",
                    workspace_mode=mode,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            session.add(
                ProjectMember(
                    id=next_id(),
                    project_id=identifier,
                    user_id=2,
                    status="active",
                    joined_at=now,
                )
            )
        session.add(
            ProjectCanvas(
                id=second.canvas,
                project_id=second.owning,
                source_project_id=first.source,
                source_key=second.key,
                title="第二角色画布",
                position=1,
                schema_version=1,
                row_version=7,
                properties_json={},
                created_by=1,
                updated_by=1,
                created_at=now,
                updated_at=now,
            )
        )
    for case in (first, second):
        source = document(case)
        with account_session(case, user_id=2) as session:
            commit(case, session, source, "7", "task-list-character")
    config_id, task_ids = next_id(), [next_id(), next_id()]
    with system_session(first) as session, session.begin():
        session.add(
            AIModelConfig(
                id=config_id,
                owner_user_id=2,
                service_type="text",
                name="仅数据库完成记录",
                provider="fixture",
                model_key="database-only",
                base_url="",
                enabled=1,
                is_deleted=0,
                is_default=0,
                row_version=1,
                created_by=2,
                updated_by=2,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        for case, task_id in zip((first, second), task_ids, strict=True):
            request = {
                "type": "canvas_text",
                "operation": "text",
                "prompt": "数据库完成夹具",
                "input": {
                    "metadata": {
                        "nodeId": "character",
                        "sourceNodeId": "character",
                        "clientOperationId": "rr-list-" + str(task_id),
                    }
                },
            }
            digest = content_hash(request)
            session.add(
                AsyncTask(
                    id=task_id,
                    project_id=case.owning,
                    scope_user_id=None,
                    initiated_by=2,
                    service_type="text",
                    status="succeeded",
                    message_status="idle",
                    idempotency_key="rr-list-" + str(task_id),
                    request_hash=digest,
                    created_at=now,
                    updated_at=now,
                    finished_at=now,
                )
            )
            session.flush()
            session.add(
                CanvasTaskBinding(
                    id=next_id(),
                    project_id=case.owning,
                    canvas_id=case.canvas,
                    initiated_by=2,
                    async_task_id=task_id,
                    node_key="character",
                    source_node_key="character",
                    client_operation_id="rr-list-" + str(task_id),
                    request_hash=digest,
                    source_snapshot={"request": request},
                    context_hash=digest,
                    created_by=2,
                    updated_by=2,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                AIGenerationRecord(
                    id=next_id(),
                    task_id=task_id,
                    call_no=1,
                    config_id=config_id,
                    config_snapshot={"provider": "fixture", "model_key": "database-only"},
                    request_data={"canvas_request": request},
                    status="succeeded",
                    text_content="已归档正文" + str(task_id),
                    created_at=now,
                    updated_at=now,
                    finished_at=now,
                )
            )
    return TaskCase(first, second, task_ids, alternate_source)


@contextmanager
def observe_reader(case, session, connections, rollback_gate=None):
    audit = {"transactions": [], "calls": [], "locks": []}
    case.observations.append({"task_list_rr": audit})
    actual_connections, transaction_objects = [], []

    def begun(actual, transaction, connection):
        transaction_objects.append(transaction)
        actual_connections.append(connection)
        identifier = connection.scalar(text("SELECT CONNECTION_ID()"))
        assert (
            connection.scalar(text("SELECT @@session.transaction_isolation")) == "REPEATABLE-READ"
        )
        connections[:] = [identifier]
        audit["transactions"].append(
            {
                "transaction": len(audit["transactions"]) + 1,
                "connection": identifier,
                "transaction_object": id(transaction),
                "outcome": "active",
            }
        )

    def rollback(_actual):
        audit["transactions"][-1]["outcome"] = "rolled_back"
        if rollback_gate is not None and len(audit["transactions"]) == 1:
            rollback_gate[0].set()
            assert rollback_gate[1].wait(15), "第二个真实holder未准备好"

    def committed(_actual):
        audit["transactions"][-1]["outcome"] = "committed"

    def statement(connection, _cursor, sql, parameters, _context, _many):
        if (
            not any(connection is actual for actual in actual_connections)
            or "FOR UPDATE" not in sql
        ):
            return
        for table in ("projects", "project_canvases", "async_tasks"):
            matched = re.search(rf"{table}\.id = %\(([^)]+)\)s", sql)
            if matched and matched[1] in parameters:
                audit["locks"].append(
                    {
                        "transaction": len(audit["transactions"]),
                        "table": table,
                        "id": int(parameters[matched[1]]),
                        "statement": sql,
                    }
                )

    def profile(frame, action, _value):
        if action != "call":
            return
        module, name = frame.f_globals.get("__name__"), frame.f_code.co_name
        if (module, name) not in {
            ("short_drama.service.canvas_character_publication_service", "lock_existing_canvases"),
            ("short_drama.service.canvas_generation_service", "_owned"),
            ("short_drama.service.canvas_generation_service", "_project"),
            ("short_drama.service.canvas_task_service", "materialize_locked"),
        }:
            return
        entry = {"transaction": len(audit["transactions"]), "method": name}
        if name == "lock_existing_canvases":
            entry["originals"] = [
                {
                    "canvas_id": str(row.id),
                    "source_id": str(row.source_project_id),
                    "row_version": row.row_version,
                }
                for row in frame.f_locals["originals"]
            ]
        else:
            entry["task_id"] = int(
                frame.f_locals["identifier"] if name == "_owned" else frame.f_locals["task"].id
            )
        audit["calls"].append(entry)

    listeners = [
        (session, "after_begin", begun),
        (session, "after_rollback", rollback),
        (session, "after_commit", committed),
        (case.engine, "before_cursor_execute", statement),
    ]
    previous = sys.getprofile()
    for target, name, callback in listeners:
        event.listen(target, name, callback)
    sys.setprofile(profile)
    try:
        yield audit
    finally:
        sys.setprofile(previous)
        for target, name, callback in reversed(listeners):
            event.remove(target, name, callback)


def list_reader(case, connections, output, *, key=None, rollback_gate=None):
    with account_session(case, user_id=2) as session:
        with observe_reader(case, session, connections, rollback_gate) as audit:
            output["audit"] = audit
            return CanvasGenerationService(
                session, Settings(_env_file=None, agent_enabled=False)
            ).list(project_id=key)


@contextmanager
def project_holder(case, project_id):
    with system_session(case) as holder:
        transaction = holder.begin()
        try:
            holder.scalar(select(Project).where(Project.id == project_id).with_for_update())
            connection = holder.connection().scalar(text("SELECT CONNECTION_ID()"))
            yield holder, transaction, connection
        finally:
            if transaction.is_active:
                transaction.rollback()


def change_version(holder, case):
    row = holder.scalar(
        select(ProjectCanvas).where(ProjectCanvas.id == case.canvas).with_for_update()
    )
    row.row_version += 1
    holder.flush()


def calls(audit, name):
    return [row for row in audit["calls"] if row["method"] == name]


def assert_retry(audit, versions, canvas_id):
    gates = calls(audit, "lock_existing_canvases")
    assert len(gates) == len(versions) == len(audit["transactions"])
    for gate, version in zip(gates, versions, strict=True):
        assert [
            row["row_version"] for row in gate["originals"] if row["canvas_id"] == str(canvas_id)
        ] == [version]
    assert len({row["transaction_object"] for row in audit["transactions"]}) == len(versions)


def assert_no_materialization(audit):
    assert (
        calls(audit, "_owned")
        == calls(audit, "_project")
        == calls(audit, "materialize_locked")
        == []
    )


def assert_lock_order(audit, projects, task_ids):
    final = len(audit["transactions"])
    locks = [row for row in audit["locks"] if row["transaction"] == final]
    first_task = next(i for i, row in enumerate(locks) if row["table"] == "async_tasks")
    project_ids = list(
        dict.fromkeys(row["id"] for row in locks[:first_task] if row["table"] == "projects")
    )
    assert project_ids == sorted(projects)
    task_order = list(dict.fromkeys(row["id"] for row in locks if row["table"] == "async_tasks"))
    assert task_order == sorted(task_ids)


def test_one_revision_race_restarts_transaction_and_materializes_each_task_once(task_case):
    case, connections, output = task_case.first, [], {}
    before = {graph.canvas: graph_state(graph) for graph in (case, task_case.second)}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with project_holder(case, case.source) as (holder, transaction, blocker):
            future = pool.submit(list_reader, case, connections, output)
            wait_for_lock(case, connections, {("projects", case.source)}, blocker)
            change_version(holder, case)
            transaction.commit()
            result = future.result(timeout=15)
    assert {int(row["id"]) for row in result} == set(task_case.task_ids)
    assert all(row["resultState"] == "READY" for row in result)
    audit = output["audit"]
    assert_retry(audit, [8, 9], case.canvas)
    assert [row["outcome"] for row in audit["transactions"]] == ["rolled_back", "committed"]
    for method in ("_owned", "_project", "materialize_locked"):
        assert sorted(row["task_id"] for row in calls(audit, method)) == sorted(task_case.task_ids)
        assert all(row["transaction"] == 2 for row in calls(audit, method))
    assert_lock_order(
        audit, {case.source, case.owning, task_case.second.owning}, task_case.task_ids
    )
    for graph in (case, task_case.second):
        state = graph_state(graph)
        assert state["counts"]["canvas_results"] == 1
        row = state["rows"]["canvas_results"][0]
        assert row["attachment_status"] == "detached" and row["row_version"] == 1
        expected = before[graph.canvas]
        expected["canvas"]["row_version"] = 9 if graph.canvas == case.canvas else 8
        state["rows"]["canvas_results"] = expected["rows"]["canvas_results"]
        state["counts"]["canvas_results"] = expected["counts"]["canvas_results"]
        assert state == expected
    assert graph_state(case)["canvas"]["row_version"] == 9
    assert graph_state(task_case.second)["canvas"]["row_version"] == 8


def test_two_revision_races_stop_at_409_without_materializing(task_case):
    case, connections, output = task_case.first, [], {}
    rolled_back, second_ready = Event(), Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with project_holder(case, case.source) as (holder, transaction, blocker):
            future = pool.submit(
                list_reader, case, connections, output, rollback_gate=(rolled_back, second_ready)
            )
            wait_for_lock(case, connections, {("projects", case.source)}, blocker)
            change_version(holder, case)
            transaction.commit()
        assert rolled_back.wait(10), "首轮真实竞争未回滚"
        with project_holder(case, case.source) as (holder, transaction, blocker):
            second_ready.set()
            wait_for_lock(case, connections, {("projects", case.source)}, blocker)
            change_version(holder, case)
            transaction.commit()
            with pytest.raises(WorkflowError) as caught:
                future.result(timeout=15)
    assert caught.value.code == "canvas_binding_changed" and caught.value.status_code == 409
    assert caught.value.message == "批量读取期间画布或可信来源已变化，请重新读取"
    audit = output["audit"]
    assert_retry(audit, [8, 9], case.canvas)
    assert [row["outcome"] for row in audit["transactions"]] == ["rolled_back", "rolled_back"]
    assert_no_materialization(audit)
    for graph in (case, task_case.second):
        assert graph_state(graph)["counts"]["canvas_results"] == 0


@pytest.mark.parametrize("mutation", ["source", "revocation"])
def test_scope_changes_are_not_retried_and_keep_complete_graph(task_case, mutation):
    case, connections, output = task_case.first, [], {}
    before = {graph.canvas: graph_state(graph) for graph in (case, task_case.second)}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with project_holder(case, case.source) as (holder, transaction, blocker):
            future = pool.submit(list_reader, case, connections, output)
            wait_for_lock(case, connections, {("projects", case.source)}, blocker)
            if mutation == "source":
                # 仅随机_test库内可信迁移改变绑定；不由HTTP或普通Actor开启系统写。
                holder.connection().execute(
                    ProjectCanvas.__table__.update()
                    .where(ProjectCanvas.id == case.canvas)
                    .values(source_project_id=task_case.alternate_source)
                )
            else:
                membership = holder.scalar(
                    select(ProjectMember)
                    .where(ProjectMember.project_id == case.source, ProjectMember.user_id == 2)
                    .with_for_update()
                )
                membership.status = "removed"
                holder.flush()
            transaction.commit()
            with pytest.raises(WorkflowError if mutation == "source" else NotFound) as caught:
                future.result(timeout=15)
    if mutation == "source":
        assert caught.value.code == "canvas_binding_changed" and caught.value.status_code == 409
        before[case.canvas]["canvas"]["source_project_id"] = task_case.alternate_source
    else:
        assert caught.value.code == "not_found" and caught.value.status_code == 404
    assert len(calls(output["audit"], "lock_existing_canvases")) == 1
    assert [row["outcome"] for row in output["audit"]["transactions"]] == ["rolled_back"]
    assert_no_materialization(output["audit"])
    assert {graph.canvas: graph_state(graph) for graph in (case, task_case.second)} == before


def real_autosave_before_commit(case, ready, release, pending):
    source = CanvasDocument.model_validate(
        {
            "id": case.key,
            "workspaceProjectId": str(case.owning),
            "projectId": str(case.source),
            "title": "合法autosave",
            "nodes": [
                {
                    "id": "plain",
                    "type": "text",
                    "title": "普通文本",
                    "position": {"x": 10, "y": 20},
                    "width": 320,
                    "height": 220,
                    "metadata": {"content": "已经保存的普通作品"},
                }
            ],
            "connections": [],
        }
    )
    with account_session(case, user_id=2) as session:

        def before_commit(actual):
            actual.flush()
            connection = actual.connection()
            assert (
                connection.scalar(
                    select(ProjectCanvas.row_version).where(ProjectCanvas.id == case.canvas)
                )
                == 8
            )
            pending["connection"] = connection.scalar(text("SELECT CONNECTION_ID()"))
            ready.set()
            assert release.wait(20), "合法autosave最后commit事件门超时"

        event.listen(session, "before_commit", before_commit)
        try:
            return commit(case, session, source, "7", "ordinary-autosave")
        finally:
            event.remove(session, "before_commit", before_commit)


def test_empty_non_character_list_retries_real_autosave_revision_race(rr_case):
    case, pending, connections, output = rr_case, {}, [], {}
    ready, release = Event(), Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(real_autosave_before_commit, case, ready, release, pending)
        try:
            if not ready.wait(8):
                if saved.done():
                    saved.result()
                raise AssertionError("合法autosave未到达真实最后commit")
            future = pool.submit(list_reader, case, connections, output, key=case.key)
            wait_for_lock(case, connections, {("projects", case.owning)}, pending["connection"])
            release.set()
            assert saved.result(timeout=15)["row_version"] == "8"
            assert future.result(timeout=15) == []
        finally:
            release.set()
    assert_retry(output["audit"], [7, 8], case.canvas)
    assert_no_materialization(output["audit"])
    with account_session(case, user_id=2) as session:
        saved_document = CanvasService(session)._document(
            session.get(ProjectCanvas, case.canvas), private=True
        )
        assert saved_document["title"] == "合法autosave"
        assert saved_document["nodes"][0]["metadata"]["content"] == "已经保存的普通作品"
