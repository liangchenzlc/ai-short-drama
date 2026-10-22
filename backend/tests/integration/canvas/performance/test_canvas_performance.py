"""真实 HTTP/MySQL 大图性能专项；显式启用，不作为生产或供应商验收。"""

import json
import math
import os
import platform
import re
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
from httpx import Response
from sqlalchemy import event, update

from short_drama.domain.canvas import CanvasRevision
from short_drama.service.base import utcnow
from short_drama.service.canvas_service import CanvasService
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CANVAS_SQL_PERFORMANCE_ENABLED") != "1",
        reason="大图性能专项需显式设置 CANVAS_SQL_PERFORMANCE_ENABLED=1",
    ),
]
WARMUP_ROUNDS = 2
MEASURED_ROUNDS = 20


class SqlMeasurements:
    def __init__(self) -> None:
        self.active: list[dict[str, Any]] | None = None
        self.snapshots: list[dict[str, Any]] = []

    def before_cursor_execute(
        self,
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if self.active is not None:
            verb = statement.lstrip().split(None, 1)[0].upper()
            tables = sorted(
                {
                    value.lower()
                    for value in re.findall(
                        r"\b(?:FROM|JOIN|UPDATE|INTO)\s+`?([a-z_][a-z0-9_]*)`?",
                        statement,
                        re.I,
                    )
                }
            )
            self.active.append(
                {
                    "verb": verb,
                    "tables": tables,
                    "bind_rows": len(parameters) if executemany else 1,
                    "edge_update": verb == "UPDATE" and tables == ["canvas_edges"],
                    "revision_body_select": verb == "SELECT"
                    and bool(
                        re.search(r"`?canvas_revisions`?\.`?snapshot_json`?", statement, re.I)
                    ),
                    "revision_insert": bool(
                        re.match(r"\s*INSERT INTO `?canvas_revisions`?\s", statement, re.I)
                    ),
                }
            )

    @staticmethod
    def sql_summary(statements: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "sql_statements": len(statements),
            "sql_by_verb": dict(Counter(item["verb"] for item in statements)),
            "sql_by_table": dict(Counter(table for item in statements for table in item["tables"])),
            "bound_rows": sum(item["bind_rows"] for item in statements),
            "edge_update_bind_rows": sum(
                item["bind_rows"] for item in statements if item["edge_update"]
            ),
            "revision_inserts": sum(item["revision_insert"] for item in statements),
            "revision_body_selects": sum(item["revision_body_select"] for item in statements),
        }

    def measure(self, operation: Callable[[], Response]) -> tuple[Response, dict[str, Any]]:
        assert self.active is None
        self.active, self.snapshots = [], []
        started = perf_counter()
        try:
            response = operation()
        finally:
            elapsed = (perf_counter() - started) * 1000
            statements, self.active = self.active, None
        return response, {
            "duration_ms": elapsed,
            "request_bytes": len(response.request.content),
            "response_bytes": len(response.content),
            **self.sql_summary(statements),
            "snapshots": deepcopy(self.snapshots),
        }


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    durations = sorted(sample["duration_ms"] for sample in samples)
    assert len(durations) == MEASURED_ROUNDS
    return {
        "samples": len(samples),
        "p50_ms": durations[math.ceil(len(durations) * 0.5) - 1],
        "p95_ms": durations[math.ceil(len(durations) * 0.95) - 1],
        "max_ms": durations[-1],
        "sql_statements_min": min(sample["sql_statements"] for sample in samples),
        "sql_statements_max": max(sample["sql_statements"] for sample in samples),
        "request_bytes_max": max(sample.get("request_bytes", 0) for sample in samples),
        "response_bytes_max": max(sample.get("response_bytes", 0) for sample in samples),
    }


def large_graph(document: dict[str, Any], count: int) -> dict[str, Any]:
    document = deepcopy(document)
    nodes = []
    for index in range(count):
        kind = "frame" if index % 10 == 9 else ("text", "image", "video", "text")[index % 4]
        node: dict[str, Any] = {
            "id": f"node-{index}",
            "type": kind,
            "title": f"数据库性能样本 {index}",
            "position": {"x": 60 + index % 10 * 300, "y": 180 + index // 10 * 230},
            "width": 240,
            "height": 160,
            "metadata": {"content": f"固定文字样本 {index}" if kind == "text" else ""},
        }
        if index % 4 == 0:
            node["metadata"]["prompt"] = f"本人参数 {index}"
        if index % 10 == 8:
            node["parentId"] = f"node-{index + 1}"
        if kind == "frame":
            node["metadata"] = {"frame": {"collapsed": False}}
        nodes.append(node)
    document["nodes"] = nodes
    document["connections"] = [
        {"id": f"edge-{index}", "fromNodeId": f"node-{index}", "toNodeId": f"node-{index + 1}"}
        for index in range(0, count - 1, 2)
    ]
    return document


@pytest.mark.parametrize("node_count", [100, 1000, 5000])
def test_large_canvas_save_and_due_snapshots_with_real_mysql(
    identity_app: Any,
    database_errors: list,
    mysql_version: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    node_count: int,
) -> None:
    client, _ = account(identity_app, f"canvas_perf_{node_count}")
    engine = identity_app[1].kw["bind"]
    assert (engine.url.database or "").endswith("_test")
    response = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": f"performance-project-{node_count}"},
        json={"name": "隔离数据库性能样本", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    response = client.get(path + "/my-document")
    assert response.status_code == 200, response.text
    document = large_graph(response.json()["source_document"], node_count)
    response = client.post(
        path + "/commits",
        headers={"Idempotency-Key": f"performance-seed-{node_count}"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert response.status_code == 200, response.text
    document["revision"] = response.json()["row_version"]
    measurements = SqlMeasurements()
    operations: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in ("save", "snapshot_generate", "current_read", "history_list", "history_read")
    }
    report: dict[str, Any] = {
        "status": "failed",
        "scope": (
            "FastAPI/TestClient、真实账号与CSRF、隔离MySQL；"
            "不含浏览器、生产网络、媒体存储、Worker或供应商"
        ),
        "node_count": node_count,
        "connection_count": len(document["connections"]),
        "warmup_rounds": WARMUP_ROUNDS,
        "measured_rounds": MEASURED_ROUNDS,
        "mysql_version": mysql_version,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "source_sha256": {
            filename: sha256(
                (Path(__file__).resolve().parents[4] / filename).read_bytes()
            ).hexdigest()
            for filename in (
                "src/short_drama/service/canvas_service.py",
                "src/short_drama/service/canvas_document.py",
                "src/short_drama/dao/canvas_dao.py",
                "src/short_drama/db/access.py",
                "src/short_drama/domain/canvas.py",
                "src/short_drama/schemas/canvas.py",
                "tests/integration/canvas/performance/test_canvas_performance.py",
            )
        },
        "snapshot_precondition": "仅在隔离库将历史created_at设为6分钟前，真实调用到期自动快照分支",
        "instrumentation": (
            "SQL事件仅记录动词、表名、次数与绑定行数，不记录参数；"
            "_snapshot观察器调用完整原方法，无模型或持久化替身"
        ),
        "samples": operations,
    }
    output = Path(os.environ.get("CANVAS_SQL_PERF_OUTPUT", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    original_snapshot = CanvasService._snapshot

    def observe_snapshot(
        service: CanvasService, canvas: Any, before: dict[str, Any], *, reason: str
    ) -> None:
        assert measurements.active is not None
        start_index = len(measurements.active)
        started = perf_counter()
        original_snapshot(service, canvas, before, reason=reason)
        measurements.snapshots.append(
            {
                "duration_ms": (perf_counter() - started) * 1000,
                **measurements.sql_summary(measurements.active[start_index:]),
            }
        )

    monkeypatch.setattr(CanvasService, "_snapshot", observe_snapshot)
    event.listen(engine, "before_cursor_execute", measurements.before_cursor_execute)
    try:
        for index in range(WARMUP_ROUNDS + MEASURED_ROUNDS):
            # 只调整本次随机测试库的时间前提，不跳过生产快照节流或持久化方法。
            with engine.begin() as connection:
                connection.execute(
                    update(CanvasRevision.__table__)
                    .where(CanvasRevision.canvas_id == int(project["primary_canvas_id"]))
                    .values(created_at=utcnow() - timedelta(minutes=6))
                )
            previous = deepcopy(document)
            document["nodes"][0]["metadata"]["content"] = f"实际保存轮次 {index}"
            response, sample = measurements.measure(
                lambda index=index: client.post(
                    path + "/commits",
                    headers={"Idempotency-Key": f"performance-save-{node_count}-{index}"},
                    json={
                        "expected_row_version": document["revision"],
                        "source_document": document,
                    },
                )
            )
            assert response.status_code == 200, response.text
            current_version = response.json()["row_version"]
            assert isinstance(current_version, str) and current_version.isdecimal()
            assert int(current_version) == int(previous["revision"]) + 1
            document["revision"] = current_version
            assert sample["revision_inserts"] == 1, "每轮必须真实写入一份快照，不能测量跳过分支"
            assert len(sample["snapshots"]) == 1
            assert sample["snapshots"][0]["revision_inserts"] == 1
            assert sample["snapshots"][0]["revision_body_selects"] == 0
            assert sample["edge_update_bind_rows"] == 0, "文字修改不得重写未变连线"
            if index >= WARMUP_ROUNDS:
                operations["save"].append(sample)
                operations["snapshot_generate"].append(sample["snapshots"][0])

            response, sample = measurements.measure(lambda: client.get(path + "/my-document"))
            assert response.status_code == 200, response.text
            current = response.json()["source_document"]
            assert current["revision"] == current_version
            assert current["nodes"] == document["nodes"]
            assert current["connections"] == document["connections"]
            if index >= WARMUP_ROUNDS:
                operations["current_read"].append(sample)

            response, sample = measurements.measure(lambda: client.get(path + "/revisions"))
            assert response.status_code == 200, response.text
            history = response.json()["items"]
            assert len(history) == min(index + 2, 20)
            revision = history[0]
            assert revision["row_version"] == previous["revision"]
            assert revision["node_count"] == node_count
            assert revision["connection_count"] == len(document["connections"])
            assert revision["payload_bytes"] > 0
            assert isinstance(revision["id"], str) and int(revision["id"]) > 2**53
            if index >= WARMUP_ROUNDS:
                operations["history_list"].append(sample)

            response, sample = measurements.measure(
                lambda revision_id=revision["id"]: client.get(path + f"/revisions/{revision_id}")
            )
            assert response.status_code == 200, response.text
            frozen = response.json()["source_document"]
            assert frozen["nodes"] == previous["nodes"]
            assert frozen["connections"] == previous["connections"]
            if index >= WARMUP_ROUNDS:
                operations["history_read"].append(sample)
            report["document_bytes"] = len(
                json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
            )
            report["latest_snapshot_payload_bytes"] = revision["payload_bytes"]
            report["history_count"] = len(history)
        assert database_errors == []
        report["summary"] = {name: summarize(samples) for name, samples in operations.items()}
        report["status"] = "measured"
    finally:
        event.remove(engine, "before_cursor_execute", measurements.before_cursor_execute)
        client.close()
        (output / f"canvas-{node_count}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
