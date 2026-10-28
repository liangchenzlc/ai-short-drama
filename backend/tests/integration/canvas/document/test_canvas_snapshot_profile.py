"""真实媒体快照单次剖析，完整执行权限guard；剖析样本不是无观察开销的p95。"""

import cProfile
import inspect
import json
import os
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
from sqlalchemy import event, func, select, update

from short_drama.domain.canvas.document.canvas import CanvasRevision, CanvasUserMediaReference
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.canvas import CanvasService
from tests.integration.canvas.document.test_canvas_reference_writes import (
    reference_rows,
    reference_scene,
)
from tests.integration.canvas.performance.test_canvas_performance import SqlMeasurements
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import ROOT, account, png
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("CANVAS_SNAPSHOT_PROFILE_ENABLED") != "1",
        reason="快照剖析需显式启用隔离MinIO及CANVAS_SNAPSHOT_PROFILE_ENABLED=1",
    ),
]


def access_callers() -> list[str]:
    frame = inspect.currentframe()
    callers = []
    try:
        while frame is not None:
            code = frame.f_code
            if code.co_filename.replace("\\", "/").endswith("/short_drama/db/access.py"):
                callers.append(code.co_name)
            frame = frame.f_back
    finally:
        del frame
    return callers


class SnapshotProfileMeasurements(SqlMeasurements):
    def __init__(self) -> None:
        super().__init__()
        self.in_snapshot = False
        self.pending: dict[int, tuple[float, dict[str, Any]]] = {}

    def before_cursor_execute(self, connection, cursor, statement, parameters, context, many):
        super().before_cursor_execute(connection, cursor, statement, parameters, context, many)
        if self.active is not None:
            item = self.active[-1]
            item["snapshot_phase"] = self.in_snapshot
            item["access_callers"] = access_callers() if self.in_snapshot else []
            item["cache_hit"] = getattr(context.cache_hit, "name", str(context.cache_hit))
            item["cursor_ms"] = None
            self.pending[id(context)] = perf_counter(), item

    def after_cursor_execute(self, connection, cursor, statement, parameters, context, many):
        pending = self.pending.pop(id(context), None)
        if pending is not None:
            started, item = pending
            item["cursor_ms"] = (perf_counter() - started) * 1000


def profile_functions(profiler: cProfile.Profile) -> list[dict[str, Any]]:
    backend = Path(__file__).resolve().parents[4].as_posix() + "/"
    functions = []
    for entry in profiler.getstats():
        code = entry.code
        if isinstance(code, str):
            filename, line, name = "<builtin>", 0, code
        else:
            filename, line, name = (
                code.co_filename.replace("\\", "/"),
                code.co_firstlineno,
                code.co_name,
            )
            if filename.startswith(backend):
                filename = filename[len(backend) :]
        functions.append(
            {
                "file": filename,
                "line": line,
                "function": name,
                "calls": entry.callcount,
                "recursive_calls": entry.reccallcount,
                "cumulative_ms": entry.totaltime * 1000,
                "self_ms": entry.inlinetime * 1000,
            }
        )
    return functions


def summarize_profile_sql(statements: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple, dict[str, Any]] = {}
    for item in statements:
        caller = tuple(item["access_callers"])
        key = item["verb"], tuple(item["tables"]), caller
        group = groups.setdefault(
            key,
            {
                "verb": item["verb"],
                "tables": item["tables"],
                "access_callers": list(caller),
                "statements": 0,
                "bound_rows": 0,
                "cursor_ms": 0.0,
            },
        )
        group["statements"] += 1
        group["bound_rows"] += item["bind_rows"]
        if item["cursor_ms"] is not None:
            group["cursor_ms"] += item["cursor_ms"]
    return {
        **SqlMeasurements.sql_summary(statements),
        "cursor_ms": sum(item["cursor_ms"] for item in statements if item["cursor_ms"] is not None),
        "incomplete_cursor_statements": sum(item["cursor_ms"] is None for item in statements),
        "cache_hits": dict(Counter(item["cache_hit"] for item in statements)),
        "groups": sorted(groups.values(), key=lambda item: item["statements"], reverse=True),
    }


@pytest.mark.parametrize("media_nodes", [2, 100, 1000])
@pytest.mark.parametrize("cache_mode", ["default", "disabled"])
def test_profile_due_media_snapshot_with_all_guards_and_real_storage(
    resource_app: Any,
    database_errors: list,
    mysql_version: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    media_nodes: int,
    cache_mode: str,
) -> None:
    owner, project, path, document, shared, private = reference_scene(
        resource_app, media_nodes, f"snapprofile_owner_{media_nodes}_{cache_mode}"
    )
    engine = resource_app[1].kw["bind"]
    original_execution_options = dict(engine.get_execution_options())
    assert (engine.url.database or "").endswith("_test")
    canvas_id = int(project["primary_canvas_id"])
    references = reference_rows(resource_app, canvas_id)
    with engine.begin() as connection:
        connection.execute(
            update(CanvasRevision.__table__)
            .where(CanvasRevision.canvas_id == canvas_id)
            .values(created_at=utcnow() - timedelta(minutes=6))
        )
    measurements = SnapshotProfileMeasurements()
    original_snapshot = CanvasService._snapshot
    report: dict[str, Any] = {
        "status": "failed",
        "scope": (
            "真实账号/CSRF、HTTP、隔离MySQL与任务MinIO；同一共享/私人PNG复用全部节点；"
            "单次到期快照诊断，剖析开销存在，不是p95、生产网络、Worker或供应商验收"
        ),
        "media_nodes": media_nodes,
        "node_count": media_nodes + 1,
        "distinct_resources": 2,
        "mysql_version": mysql_version,
        "compiled_cache_mode": cache_mode,
        "initial_graph_in_timing": False,
        "source_sha256": {
            name: sha256((Path(__file__).resolve().parents[4] / name).read_bytes()).hexdigest()
            for name in (
                "src/short_drama/service/canvas_service.py",
                "src/short_drama/dao/canvas_dao.py",
                "src/short_drama/dao/canvas_resource_dao.py",
                "src/short_drama/db/access.py",
                "tests/integration/canvas/performance/test_canvas_performance.py",
                "tests/integration/canvas/document/test_canvas_reference_writes.py",
                "tests/integration/canvas/document/test_canvas_snapshot_profile.py",
            )
        },
        "instrumentation": (
            "cProfile在实际执行快照的API线程内启停，调用完整原方法；SQL事件记录次数、表名、"
            "动词、cursor耗时与编译缓存命中；调用链只读代码位置，不存SQL文本/参数/局部变量"
        ),
    }
    output = Path(os.environ.get("CANVAS_SNAPSHOT_PROFILE_OUTPUT", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)

    def observe_snapshot(service, canvas, before, *, reason):
        assert measurements.active is not None
        start_index = len(measurements.active)
        profiler = cProfile.Profile()
        if cache_mode == "disabled":
            service.session.connection().execution_options(compiled_cache=None)
        measurements.in_snapshot = True
        started = perf_counter()
        profiler.enable()
        try:
            original_snapshot(service, canvas, before, reason=reason)
        finally:
            profiler.disable()
            duration_ms = (perf_counter() - started) * 1000
            measurements.in_snapshot = False
            functions = profile_functions(profiler)
            report["snapshot"] = {
                "duration_ms": duration_ms,
                "sql": summarize_profile_sql(measurements.active[start_index:]),
                "top_self": sorted(functions, key=lambda item: item["self_ms"], reverse=True)[:40],
                "top_cumulative": sorted(
                    functions, key=lambda item: item["cumulative_ms"], reverse=True
                )[:40],
                "project_functions": [
                    item for item in functions if item["file"].startswith("src/short_drama/")
                ],
            }

    monkeypatch.setattr(CanvasService, "_snapshot", observe_snapshot)
    event.listen(engine, "before_cursor_execute", measurements.before_cursor_execute)
    event.listen(engine, "after_cursor_execute", measurements.after_cursor_execute)
    try:
        previous = deepcopy(document)
        document["nodes"][0]["metadata"]["content"] = "实际到期快照剖析"
        response, sample = measurements.measure(
            lambda: owner.post(
                path + "/commits",
                headers={"Idempotency-Key": f"snapprofile-due-{media_nodes}"},
                json={
                    "expected_row_version": document["revision"],
                    "source_document": document,
                },
            )
        )
        report["save"] = sample
        assert response.status_code == 200, response.text
        assert int(response.json()["row_version"]) == int(document["revision"]) + 1
        assert sample["revision_inserts"] == 1
        assert report["snapshot"]["sql"]["revision_inserts"] == 1
        assert report["snapshot"]["sql"]["revision_body_selects"] == 0
        assert report["snapshot"]["sql"]["incomplete_cursor_statements"] == 0
        assert measurements.pending == {}
        if cache_mode == "disabled":
            assert report["snapshot"]["sql"]["cache_hits"].get("CACHING_DISABLED", 0) > 0
        assert any(
            "guard_writes" in group["access_callers"]
            for group in report["snapshot"]["sql"]["groups"]
        ), "剖析必须覆盖实际guard，不得以替身或关闭检查缩减耗时"
        assert reference_rows(resource_app, canvas_id) == references
        history = owner.get(path + "/revisions").json()["items"]
        assert len(history) == 2
        frozen = owner.get(path + f"/revisions/{history[0]['id']}").json()["source_document"]
        assert frozen["nodes"] == previous["nodes"]
        assert (
            owner.get(path + "/my-document").json()["source_document"]["nodes"] == document["nodes"]
        )
        with resource_app[1]() as session:
            assert dict(session.connection().get_execution_options()) == original_execution_options
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(CanvasUserMediaReference)
                    .where(CanvasUserMediaReference.revision_id == int(history[0]["id"]))
                )
                == media_nodes
            )
        assert owner.get(f"{ROOT}/{shared['id']}/file").content == png("red")
        assert owner.get(f"{ROOT}/{private['id']}/file").content == png("blue")
        assert database_errors == []
        report["status"] = "profiled"
    finally:
        event.remove(engine, "before_cursor_execute", measurements.before_cursor_execute)
        event.remove(engine, "after_cursor_execute", measurements.after_cursor_execute)
        owner.close()
        suffix = "-disabled" if cache_mode == "disabled" else ""
        (output / f"snapshot-profile-{media_nodes}{suffix}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def test_snapshot_cache_scope_preserves_owner_privacy_and_later_connections(
    resource_app: Any, database_errors: list
) -> None:
    owner, project, path, document, shared, private = reference_scene(
        resource_app, 2, "snapcache_scope_owner"
    )
    outsider, _ = account(resource_app, "snapcache_scope_other")
    engine = resource_app[1].kw["bind"]
    assert (engine.url.database or "").endswith("_test")
    original_execution_options = dict(engine.get_execution_options())
    try:
        response = owner.get(path + "/my-document")
        assert response.status_code == 200, response.text
        owner_document = response.json()["source_document"]
        assert owner_document["nodes"] == document["nodes"]
        with engine.begin() as connection:
            connection.execute(
                update(CanvasRevision.__table__)
                .where(CanvasRevision.canvas_id == int(project["primary_canvas_id"]))
                .values(created_at=utcnow() - timedelta(minutes=6))
            )
        owner_document["nodes"][0]["metadata"]["content"] = "本人真实到期保存"
        cache_flags = []

        def observe_cache(connection, cursor, statement, parameters, context, many):
            cache_flags.append(context.cache_hit.name)

        event.listen(engine, "before_cursor_execute", observe_cache)
        try:
            response = owner.post(
                path + "/commits",
                headers={"Idempotency-Key": "snapcache-owner-due"},
                json={
                    "expected_row_version": owner_document["revision"],
                    "source_document": owner_document,
                },
            )
        finally:
            event.remove(engine, "before_cursor_execute", observe_cache)
        assert response.status_code == 200, response.text
        assert "CACHING_DISABLED" in cache_flags, "真实到期保存应采用已验证的编译策略"
        with resource_app[1]() as session:
            assert dict(session.connection().get_execution_options()) == original_execution_options
        assert dict(engine.get_execution_options()) == original_execution_options
        current = owner.get(path + "/my-document").json()["source_document"]
        assert current["nodes"][1:] == document["nodes"][1:]
        history = owner.get(path + "/revisions").json()["items"]
        assert len(history) == 2
        frozen = owner.get(path + f"/revisions/{history[0]['id']}").json()["source_document"]
        assert frozen["nodes"] == document["nodes"]
        assert owner.get(f"{ROOT}/{shared['id']}/file").content == png("red")
        assert owner.get(f"{ROOT}/{private['id']}/file").content == png("blue")
        assert outsider.get(path).status_code == 404
        assert outsider.get(path + "/my-document").status_code == 404
        assert outsider.get(path + f"/revisions/{history[0]['id']}").status_code == 404
        assert outsider.get(f"{ROOT}/{shared['id']}/file").status_code == 404
        assert outsider.get(f"{ROOT}/{private['id']}/file").status_code == 404
        assert database_errors == []
    finally:
        owner.close()
        outsider.close()
