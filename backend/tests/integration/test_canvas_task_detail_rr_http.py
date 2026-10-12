"""真实 MySQL/RR 与公开 ASGI 路由；无 TCP、浏览器、模型或对象 I/O。"""

from __future__ import annotations

import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text

from short_drama.core.config import Settings
from short_drama.db.session import AppSession, session_factory
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    ProjectCanvas,
)
from short_drama.main import create_app
from short_drama.service.base import utcnow
from short_drama.service.canvas_document import content_hash
from short_drama.utils.snowflake import next_id
from tests.integration.test_canvas_character_rr import RRCase, wait_for_lock
from tests.integration.test_identity_collaboration import account

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_CHARACTER_RR") != "1",
        reason="RUN_CANVAS_CHARACTER_RR=1 显式启用真实 MySQL RR 回归",
    ),
]
REQUEST_KIND = ContextVar("detail_rr_request_kind", default="other")


class ObservedASGI:
    """只按测试自有头标记请求上下文，完整透传原 ASGI app 与收发事件。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        kind = headers.get(b"x-detail-rr-observation", b"other").decode("ascii")
        token = REQUEST_KIND.set(kind)
        try:
            await self.app(scope, receive, send)
        finally:
            REQUEST_KIND.reset(token)


def seed_completed_task(factory, user_id, project_id, canvas_id):
    """只在独占随机_test库中创建已完成文本记录；没有模型或对象 I/O。"""
    now, config_id, task_id = utcnow(), next_id(), next_id()
    request = {
        "type": "canvas_text",
        "operation": "text",
        "prompt": "数据库完成夹具",
        "input": {
            "metadata": {
                "nodeId": "ordinary",
                "sourceNodeId": "ordinary",
                "clientOperationId": "rr-detail-" + str(task_id),
            }
        },
    }
    digest = content_hash(request)
    with factory() as session, session.begin():
        session.add(
            AIModelConfig(
                id=config_id,
                owner_user_id=user_id,
                service_type="text",
                name="仅数据库完成记录",
                provider="fixture",
                model_key="database-only",
                base_url="",
                enabled=1,
                is_deleted=0,
                is_default=0,
                row_version=1,
                created_by=user_id,
                updated_by=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            AsyncTask(
                id=task_id,
                project_id=project_id,
                scope_user_id=None,
                initiated_by=user_id,
                service_type="text",
                status="succeeded",
                message_status="idle",
                idempotency_key="rr-detail-" + str(task_id),
                request_hash=digest,
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
        )
        session.flush()
        binding = CanvasTaskBinding(
            id=next_id(),
            project_id=project_id,
            canvas_id=canvas_id,
            initiated_by=user_id,
            async_task_id=task_id,
            node_key="ordinary",
            source_node_key="ordinary",
            client_operation_id="rr-detail-" + str(task_id),
            request_hash=digest,
            source_snapshot={"request": request},
            context_hash=digest,
            created_by=user_id,
            updated_by=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(binding)
        session.add(
            AIGenerationRecord(
                id=next_id(),
                task_id=task_id,
                call_no=1,
                config_id=config_id,
                config_snapshot={"provider": "fixture", "model_key": "database-only"},
                request_data={"canvas_request": request},
                status="succeeded",
                text_content="已归档的数据库夹具正文",
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
        )
    return task_id


def state(factory, canvas_id, task_id):
    with factory() as session:
        canvas = session.get(ProjectCanvas, canvas_id)
        task = session.get(AsyncTask, task_id)
        return {
            "canvas": {
                "id": str(canvas.id),
                "project_id": str(canvas.project_id),
                "source_project_id": str(canvas.source_project_id)
                if canvas.source_project_id is not None
                else None,
                "source_key": canvas.source_key,
                "row_version": canvas.row_version,
            },
            "task": {
                "id": str(task.id),
                "status": task.status,
                "message_status": task.message_status,
                "message_version": task.message_version,
                "initiated_by": str(task.initiated_by),
            },
            "results": session.scalar(
                select(func.count())
                .select_from(CanvasResult)
                .join(CanvasTaskBinding, CanvasTaskBinding.id == CanvasResult.task_binding_id)
                .where(CanvasTaskBinding.async_task_id == task_id)
            ),
        }


def test_public_detail_stays_available_after_legitimate_autosave_revision_race(
    db_session, mysql_engine, tmp_path, request
):
    assert db_session.get_bind() is mysql_engine and mysql_engine.dialect.name == "mysql"
    assert mysql_engine.url.database.endswith("_test")
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        agent_enabled=False,
        auth_cookie_secure=False,
        public_origin="http://testserver",
        encryption_key=base64.b64encode(b"r" * 32).decode(),
        smtp_host="",
        smtp_from="",
    )
    factory = session_factory(mysql_engine)
    app = create_app(settings)
    app.state.session_factory, app.state.storage = factory, None
    observed_app = ObservedASGI(app)
    client, user = account((observed_app, factory, settings), "detail_rr_" + uuid4().hex[:12])
    reader = TestClient(observed_app)
    reader.cookies.update(client.cookies)
    reader.headers.update(client.headers)
    audit = {
        "scope": "真实公开HTTP autosave vs GET detail；仅观测不替换业务",
        "transactions": [],
        "locks": [],
        "canvas_reads": [],
        "provider_calls": 0,
        "backend_env_read": False,
    }
    connections, pending, transaction_rows = [], {}, {}
    ready, release = Event(), Event()
    listeners = []
    try:
        response = client.post(
            "/api/v1/projects",
            headers={"Idempotency-Key": "detail-project-" + uuid4().hex},
            json={"name": "任务详情RR", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
        )
        assert response.status_code == 201, response.text
        project = response.json()
        project_id, canvas_id = int(project["id"]), int(project["primary_canvas_id"])
        path = f"/api/v1/projects/{project_id}/canvases/{canvas_id}"
        source = client.get(path + "/my-document").json()["source_document"]
        source["nodes"] = [
            {
                "id": "ordinary",
                "type": "text",
                "title": "普通文本",
                "position": {"x": 10, "y": 20},
                "width": 320,
                "height": 220,
                "metadata": {"content": "已保存的普通作品"},
            }
        ]
        response = client.post(
            path + "/commits",
            headers={"Idempotency-Key": "detail-source-" + uuid4().hex},
            json={"expected_row_version": source["revision"], "source_document": source},
        )
        assert response.status_code == 200, response.text
        source = client.get(path + "/my-document").json()["source_document"]
        task_id = seed_completed_task(factory, int(user["id"]), project_id, canvas_id)
        task_path = "/api/v1/canvas-runtime/tasks/" + str(task_id)
        baseline = reader.get(task_path, headers={"X-Detail-RR-Observation": "control"})
        audit["control_detail"] = {"status": baseline.status_code, "body": baseline.json()}
        assert baseline.status_code == 200, baseline.text
        before = state(factory, canvas_id, task_id)
        changed = deepcopy(source)
        changed["title"] = "合法autosave后的标题"
        audit["before"] = before
        case = RRCase(mysql_engine, 0, project_id, canvas_id, 0, 0, source["id"])

        def begun(actual, transaction, connection):
            kind = REQUEST_KIND.get()
            if kind not in {"autosave", "detail"}:
                return
            row = {
                "kind": kind,
                "session": id(actual),
                "transaction_object": id(transaction),
                "connection": connection.scalar(text("SELECT CONNECTION_ID()")),
                "isolation": connection.scalar(text("SELECT @@session.transaction_isolation")),
                "outcome": "active",
            }
            assert row["isolation"] == "REPEATABLE-READ"
            audit["transactions"].append(row)
            transaction_rows[id(actual)] = row

        def completed(actual, outcome):
            if id(actual) in transaction_rows:
                transaction_rows[id(actual)]["outcome"] = outcome

        def before_commit(actual):
            if REQUEST_KIND.get() != "autosave" or ready.is_set():
                return
            canvases = [
                item
                for item in actual.identity_map.values()
                if isinstance(item, ProjectCanvas) and item.id == canvas_id
            ]
            if not canvases or canvases[0].row_version != before["canvas"]["row_version"] + 1:
                return
            actual.flush()
            connection = actual.connection()
            pending["connection"] = connection.scalar(text("SELECT CONNECTION_ID()"))
            pending["new_revision"] = connection.scalar(
                select(ProjectCanvas.row_version).where(ProjectCanvas.id == canvas_id)
            )
            ready.set()
            assert release.wait(20), "公开autosave最后commit事件门超时"

        def statement(connection, _cursor, sql, parameters, _context, _many):
            if REQUEST_KIND.get() != "detail" or "FOR UPDATE" not in sql:
                return
            table = next(
                (
                    name
                    for name in ("projects", "project_canvases", "async_tasks")
                    if re.search(rf"{name}\.id = %\(([^)]+)\)s", sql)
                ),
                None,
            )
            if table is None:
                return
            match = re.search(rf"{table}\.id = %\(([^)]+)\)s", sql)
            identifier = int(parameters[match[1]])
            row = {
                "table": table,
                "id": str(identifier),
                "connection": connection.scalar(text("SELECT CONNECTION_ID()")),
                "sql": sql,
            }
            audit["locks"].append(row)
            if table == "projects" and identifier == project_id:
                connections[:] = [row["connection"]]

        def canvas_read(actual, _context, _attrs=None):
            if REQUEST_KIND.get() == "detail" and actual.id == canvas_id:
                audit["canvas_reads"].append(
                    {
                        "canvas_id": str(actual.id),
                        "project_id": str(actual.project_id),
                        "source_project_id": str(actual.source_project_id)
                        if actual.source_project_id is not None
                        else None,
                        "row_version": actual.row_version,
                    }
                )

        listeners = [
            (AppSession, "after_begin", begun),
            (AppSession, "after_commit", lambda actual: completed(actual, "committed")),
            (AppSession, "after_rollback", lambda actual: completed(actual, "rolled_back")),
            (AppSession, "before_commit", before_commit),
            (mysql_engine, "before_cursor_execute", statement),
            (ProjectCanvas, "load", canvas_read),
            (ProjectCanvas, "refresh", canvas_read),
        ]
        for target, name, callback in listeners:
            event.listen(target, name, callback)
        with ThreadPoolExecutor(max_workers=2) as pool:
            saved = pool.submit(
                client.post,
                path + "/commits",
                headers={
                    "Idempotency-Key": "detail-autosave-" + uuid4().hex,
                    "X-Detail-RR-Observation": "autosave",
                },
                json={"expected_row_version": source["revision"], "source_document": changed},
            )
            try:
                if not ready.wait(8):
                    if saved.done():
                        failed = saved.result()
                        audit["autosave_before_wait"] = {
                            "status": failed.status_code,
                            "body": failed.json(),
                        }
                    raise AssertionError("公开autosave未到达真实最后commit，未形成竞争")
                future = pool.submit(
                    reader.get, task_path, headers={"X-Detail-RR-Observation": "detail"}
                )
                wait_for_lock(case, connections, {("projects", project_id)}, pending["connection"])
                audit["waits"] = case.observations
                release.set()
                saved_response = saved.result(timeout=15)
                detail_response = future.result(timeout=15)
            finally:
                release.set()
        audit["autosave"] = {
            "status": saved_response.status_code,
            "body": saved_response.json(),
            "pending": pending,
        }
        audit["raced_detail"] = {
            "method": "GET",
            "path": task_path,
            "status": detail_response.status_code,
            "body": detail_response.json(),
        }
        assert before["canvas"]["row_version"] in {
            item["row_version"] for item in audit["canvas_reads"]
        }, "必须真实读取autosave提交前的RR版本"
        after = state(factory, canvas_id, task_id)
        audit["after"] = after
        fresh = reader.get(task_path)
        audit["fresh_detail"] = {"status": fresh.status_code, "body": fresh.json()}
        final_document = client.get(path + "/my-document").json()["source_document"]
        audit["saved_document"] = {
            "revision": final_document["revision"],
            "title": final_document["title"],
            "nodes": final_document["nodes"],
        }
        assert saved_response.status_code == 200, saved_response.text
        assert int(saved_response.json()["row_version"]) == before["canvas"]["row_version"] + 1
        expected = deepcopy(before)
        expected["canvas"]["row_version"] += 1
        assert after == expected, "绑定范围/任务/结果必须保持，仅画布row_version推进"
        assert fresh.status_code == 200, fresh.text
        assert (
            final_document["title"] == changed["title"]
            and final_document["nodes"] == source["nodes"]
        )
        assert detail_response.status_code == 200, (
            "合法autosave版本竞争后详情应可读取；实际响应=" + detail_response.text
        )
        assert detail_response.json()["resultState"] == "READY"
    finally:
        release.set()
        for target, name, callback in reversed(listeners):
            event.remove(target, name, callback)
        client.close()
        reader.close()
        request.node.user_properties.append(
            ("detail_rr_http_observations", json.dumps(audit, ensure_ascii=False))
        )
        (tmp_path / "detail-rr-http-observations.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
