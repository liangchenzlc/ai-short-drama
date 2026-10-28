"""真实永久媒体的到期快照及满历史保存专项；不作为生产或供应商验收。"""

import json
import os
import platform
import re
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from short_drama.domain.canvas.document.canvas import (
    CanvasMediaReference,
    CanvasRevision,
    CanvasRevisionMediaReference,
    CanvasRevisionUserState,
    CanvasUserMediaReference,
)
from short_drama.domain.canvas.resources.resource import (
    CanvasBinaryReference,
    CanvasUserBinaryReference,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.canvas import CanvasService
from tests.integration.canvas.document.test_canvas_reference_writes import (
    reference_rows,
    reference_scene,
    saved_document,
)
from tests.integration.canvas.performance.test_canvas_performance import (
    MEASURED_ROUNDS,
    WARMUP_ROUNDS,
    SqlMeasurements,
    summarize,
)
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import (
    ROOT,
    account,
    canvas,
    png,
    upload,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("CANVAS_SQL_PERFORMANCE_ENABLED") != "1",
        reason="真实媒体性能专项需显式启用隔离MinIO及CANVAS_SQL_PERFORMANCE_ENABLED=1",
    ),
]
REFERENCE_MODELS = (
    CanvasMediaReference,
    CanvasUserMediaReference,
    CanvasBinaryReference,
    CanvasUserBinaryReference,
)


class ReferenceMeasurements(SqlMeasurements):
    def __init__(self) -> None:
        super().__init__()
        self.history_reference_loads = 0

    def before_cursor_execute(self, connection, cursor, statement, parameters, context, many):
        super().before_cursor_execute(connection, cursor, statement, parameters, context, many)
        if self.active is not None:
            item = self.active[-1]
            item["resource_lookup_select"] = (
                item["verb"] == "SELECT"
                and bool(set(item["tables"]) & {"media_files", "canvas_binary_resources"})
                and not re.search(r"\b(?:FOR SHARE|LOCK IN SHARE MODE)\b", statement, re.I)
            )
            item["reference_update"] = item["verb"] == "UPDATE" and bool(
                set(item["tables"]) & {model.__tablename__ for model in REFERENCE_MODELS}
            )

    def loaded_as_persistent(self, session: Session, value: Any) -> None:
        if (
            self.active is not None
            and isinstance(value, REFERENCE_MODELS)
            and value.owner_kind == "revision"
        ):
            self.history_reference_loads += 1

    @staticmethod
    def sql_summary(statements: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            **SqlMeasurements.sql_summary(statements),
            "resource_lookup_selects": sum(item["resource_lookup_select"] for item in statements),
            "reference_update_bind_rows": sum(
                item["bind_rows"] for item in statements if item["reference_update"]
            ),
        }

    def measure(self, operation):
        self.history_reference_loads = 0
        response, sample = super().measure(operation)
        sample["history_reference_loads"] = self.history_reference_loads
        return response, sample


def reference_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **summarize(samples),
        **{
            f"{field}_{bound}": operation(sample[field] for sample in samples)
            for field in (
                "history_reference_loads",
                "resource_lookup_selects",
                "reference_update_bind_rows",
            )
            for bound, operation in (("min", min), ("max", max))
        },
    }


@pytest.mark.parametrize("media_nodes", [2, 100, 1000])
def test_due_media_snapshots_and_twenty_history_references_with_real_storage(
    resource_app: Any,
    database_errors: list,
    mysql_version: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    media_nodes: int,
) -> None:
    owner, project, path, document, shared, private = reference_scene(
        resource_app, media_nodes, f"refperf_owner_{media_nodes}"
    )
    engine = resource_app[1].kw["bind"]
    assert (engine.url.database or "").endswith("_test")
    canvas_id = int(project["primary_canvas_id"])
    current_references = reference_rows(resource_app, canvas_id)
    measurements = ReferenceMeasurements()
    operations: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "save_due",
            "snapshot_generate",
            "save_recent_full_history",
            "current_read_full_history",
            "history_list_full_history",
            "history_read_full_history",
        )
    }
    report: dict[str, Any] = {
        "status": "failed",
        "scope": (
            "FastAPI/TestClient、真实账号/CSRF、隔离MySQL和任务MinIO桶；两个实际PNG资源，"
            "同一作品和私人参考分别复用到全部图片节点；不含浏览器、生产网络、Worker或供应商"
        ),
        "media_nodes": media_nodes,
        "node_count": media_nodes + 1,
        "distinct_resources": 2,
        "warmup_rounds": WARMUP_ROUNDS,
        "measured_rounds": MEASURED_ROUNDS,
        "mysql_version": mysql_version,
        "python_version": platform.python_version(),
        "source_sha256": {
            filename: sha256(
                (Path(__file__).resolve().parents[4] / filename).read_bytes()
            ).hexdigest()
            for filename in (
                "src/short_drama/service/canvas_service.py",
                "src/short_drama/dao/canvas_dao.py",
                "src/short_drama/dao/canvas_resource_dao.py",
                "src/short_drama/db/access.py",
                "tests/integration/canvas/performance/test_canvas_reference_performance.py",
            )
        },
        "snapshot_precondition": "仅在随机测试库回拨created_at；完整调用生产快照及权限guard",
        "due_history_counts": [],
        "instrumentation": (
            "SQL事件记录动词、表名、绑定行数，不存参数；ORM载入事件只计历史引用行；"
            "普通资源读取和guard的FOR SHARE当前行校验分开计数，均实际执行"
        ),
        "samples": operations,
    }
    output = Path(os.environ.get("CANVAS_REFERENCE_PERF_OUTPUT", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    original_snapshot = CanvasService._snapshot

    def observe_snapshot(service, canvas, before, *, reason):
        assert measurements.active is not None
        start_index = len(measurements.active)
        history_loads = measurements.history_reference_loads
        started = perf_counter()
        original_snapshot(service, canvas, before, reason=reason)
        measurements.snapshots.append(
            {
                "duration_ms": (perf_counter() - started) * 1000,
                **measurements.sql_summary(measurements.active[start_index:]),
                "history_reference_loads": measurements.history_reference_loads - history_loads,
            }
        )

    monkeypatch.setattr(CanvasService, "_snapshot", observe_snapshot)
    event.listen(engine, "before_cursor_execute", measurements.before_cursor_execute)
    event.listen(Session, "loaded_as_persistent", measurements.loaded_as_persistent)
    try:
        for index in range(WARMUP_ROUNDS + MEASURED_ROUNDS):
            with engine.begin() as connection:
                connection.execute(
                    update(CanvasRevision.__table__)
                    .where(CanvasRevision.canvas_id == canvas_id)
                    .values(created_at=utcnow() - timedelta(minutes=6))
                )
            frozen_document = deepcopy(document)
            document["nodes"][0]["metadata"]["content"] = f"真实到期保存 {index}"
            response, sample = measurements.measure(
                lambda index=index: owner.post(
                    path + "/commits",
                    headers={"Idempotency-Key": f"refperf-due-{media_nodes}-{index}"},
                    json={
                        "expected_row_version": document["revision"],
                        "source_document": document,
                    },
                )
            )
            assert response.status_code == 200, response.text
            document["revision"] = response.json()["row_version"]
            assert int(document["revision"]) == int(frozen_document["revision"]) + 1
            assert sample["revision_inserts"] == 1
            assert len(sample["snapshots"]) == 1
            assert sample["snapshots"][0]["revision_body_selects"] == 0
            assert sample["reference_update_bind_rows"] == 0
            if index >= WARMUP_ROUNDS:
                operations["save_due"].append(sample)
                operations["snapshot_generate"].append(sample["snapshots"][0])
            response = owner.get(path + "/revisions")
            assert response.status_code == 200, response.text
            history = response.json()["items"]
            assert len(history) == min(index + 2, 20)
            report["due_history_counts"].append(len(history))
            latest = history[0]
            assert latest["row_version"] == frozen_document["revision"]

        for index in range(WARMUP_ROUNDS + MEASURED_ROUNDS):
            document["nodes"][0]["metadata"]["content"] = f"20项真实历史后保存 {index}"
            response, sample = measurements.measure(
                lambda index=index: owner.post(
                    path + "/commits",
                    headers={"Idempotency-Key": f"refperf-recent-{media_nodes}-{index}"},
                    json={
                        "expected_row_version": document["revision"],
                        "source_document": document,
                    },
                )
            )
            assert response.status_code == 200, response.text
            assert sample["revision_inserts"] == 0
            assert sample["reference_update_bind_rows"] == 0
            document["revision"] = response.json()["row_version"]
            if index >= WARMUP_ROUNDS:
                operations["save_recent_full_history"].append(sample)

            response, sample = measurements.measure(lambda: owner.get(path + "/my-document"))
            assert response.status_code == 200, response.text
            assert response.json()["source_document"]["nodes"] == document["nodes"]
            if index >= WARMUP_ROUNDS:
                operations["current_read_full_history"].append(sample)

            response, sample = measurements.measure(lambda: owner.get(path + "/revisions"))
            assert response.status_code == 200, response.text
            assert response.json()["items"] == history
            if index >= WARMUP_ROUNDS:
                operations["history_list_full_history"].append(sample)

            response, sample = measurements.measure(
                lambda: owner.get(path + f"/revisions/{latest['id']}")
            )
            assert response.status_code == 200, response.text
            assert response.json()["source_document"]["nodes"] == frozen_document["nodes"]
            if index >= WARMUP_ROUNDS:
                operations["history_read_full_history"].append(sample)

        assert reference_rows(resource_app, canvas_id) == current_references
        with resource_app[1]() as session:
            private_counts = dict(
                session.execute(
                    select(CanvasUserMediaReference.revision_id, func.count())
                    .where(
                        CanvasUserMediaReference.canvas_id == canvas_id,
                        CanvasUserMediaReference.owner_kind == "revision",
                    )
                    .group_by(CanvasUserMediaReference.revision_id)
                ).all()
            )
            assert set(private_counts) == {int(item["id"]) for item in history}
            assert set(private_counts.values()) == {media_nodes}
            shared_counts = dict(
                session.execute(
                    select(CanvasRevisionMediaReference.revision_id, func.count())
                    .where(CanvasRevisionMediaReference.canvas_id == canvas_id)
                    .group_by(CanvasRevisionMediaReference.revision_id)
                ).all()
            )
            assert shared_counts.keys() == private_counts.keys()
            assert set(shared_counts.values()) == {1}
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(CanvasRevisionUserState)
                    .where(CanvasRevisionUserState.canvas_id == canvas_id)
                )
                == 20
            )
        assert owner.get(f"{ROOT}/{shared['id']}/file").content == png("red")
        assert owner.get(f"{ROOT}/{private['id']}/file").content == png("blue")
        member, member_user = account(resource_app, f"refperf_member_{media_nodes}")
        outsider, _ = account(resource_app, f"refperf_other_{media_nodes}")
        try:
            response = member.get(path + f"/revisions/{latest['id']}")
            assert response.status_code == 404, response.text
            assert member.get(f"{ROOT}/{shared['id']}/file").status_code == 404
            assert member.get(f"{ROOT}/{private['id']}/file").status_code == 404
            assert outsider.get(path + f"/revisions/{latest['id']}").status_code == 404
        finally:
            member.close()
            outsider.close()
        assert database_errors == []
        report["summary"] = {
            name: reference_summary(samples) for name, samples in operations.items()
        }
        report["retained_history"] = 20
        report["retained_private_reference_rows"] = sum(private_counts.values())
        report["retained_shared_reference_rows"] = sum(shared_counts.values())
        report["performance_checks"] = {
            "snapshot_lookup_selects_max": max(
                sample["resource_lookup_selects"] for sample in operations["snapshot_generate"]
            ),
            "full_history_reference_loads_max": max(
                sample["history_reference_loads"]
                for sample in operations["save_recent_full_history"]
            ),
        }
        assert report["performance_checks"]["snapshot_lookup_selects_max"] <= 2, (
            "快照普通资源查询须按身份批量读取，guard当前行校验仍完整执行"
        )
        assert report["performance_checks"]["full_history_reference_loads_max"] == 0, (
            "普通保存不得载入20份历史引用"
        )
        report["status"] = "measured"
    finally:
        event.remove(engine, "before_cursor_execute", measurements.before_cursor_execute)
        event.remove(Session, "loaded_as_persistent", measurements.loaded_as_persistent)
        owner.close()
        (output / f"media-history-{media_nodes}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def test_mixed_binary_snapshot_preserves_each_private_path_and_foreign_scope_rollback(
    resource_app: Any, database_errors: list
) -> None:
    owner, project, path, document, _, _ = reference_scene(resource_app, 2, "refperf_binary_owner")
    member, member_user = account(resource_app, "refperf_binary_member")
    outsider, _ = account(resource_app, "refperf_binary_other")
    try:
        resources = []
        bodies = [b"glTF" + bytes([value]) * 64 for value in (1, 2)]
        for index, body in enumerate(bodies):
            response = upload(
                owner,
                body,
                key=f"refperf-binary-{index}",
                source=document["id"],
                kind="file",
                name="model.glb",
                mime="model/gltf-binary",
            )
            assert response.status_code == 200, response.text
            resources.append(response.json()["resource"])
        shared, private = resources
        document["directorScenes"] = [
            {
                "id": "binary-scene",
                "model": f"resource:{shared['id']}",
                "taskConfig": {"referenceResourceIds": [private["id"], private["id"]]},
            }
        ]
        document = saved_document(owner, path, document, "refperf-binary-attach")
        engine = resource_app[1].kw["bind"]
        assert (engine.url.database or "").endswith("_test")
        canvas_id = int(project["primary_canvas_id"])
        with engine.begin() as connection:
            connection.execute(
                update(CanvasRevision.__table__)
                .where(CanvasRevision.canvas_id == canvas_id)
                .values(created_at=utcnow() - timedelta(minutes=6))
            )
        frozen = deepcopy(document)
        document["nodes"][0]["metadata"]["content"] = "混合媒体实际到期快照"
        document = saved_document(owner, path, document, "refperf-binary-due")
        response = owner.get(path + "/revisions")
        assert response.status_code == 200, response.text
        history = response.json()["items"]
        revision_id = int(history[0]["id"])
        with resource_app[1]() as session:
            shared_rows = session.scalars(
                select(CanvasBinaryReference).where(
                    CanvasBinaryReference.revision_id == revision_id
                )
            ).all()
            assert len(shared_rows) == 1 and shared_rows[0].binary_id == int(shared["id"])
            private_rows = session.scalars(
                select(CanvasUserBinaryReference).where(
                    CanvasUserBinaryReference.revision_id == revision_id
                )
            ).all()
            assert len(private_rows) == 2, "同一私人文件的两个路径必须分别冻结"
            assert len({row.slot for row in private_rows}) == 2
            assert {row.binary_id for row in private_rows} == {int(private["id"])}
        response = owner.get(path + f"/revisions/{revision_id}")
        assert response.status_code == 200, response.text
        assert response.json()["source_document"]["directorScenes"] == frozen["directorScenes"]
        response = member.get(path + f"/revisions/{revision_id}")
        assert response.status_code == 404, response.text
        assert member.get(f"{ROOT}/{shared['id']}/file").status_code == 404
        assert owner.get(f"{ROOT}/{private['id']}/file").content == bodies[1]
        assert member.get(f"{ROOT}/{private['id']}/file").status_code == 404
        assert outsider.get(path + f"/revisions/{revision_id}").status_code == 404
        _, _, foreign_document = canvas(owner, "refperf-binary-foreign-project")
        response = upload(
            owner,
            b"glTF" + bytes(64),
            key="refperf-binary-foreign",
            source=foreign_document["id"],
            kind="file",
            name="foreign.glb",
            mime="model/gltf-binary",
        )
        assert response.status_code == 200, response.text
        invalid = deepcopy(document)
        invalid["directorScenes"][0]["model"] = f"resource:{response.json()['resource']['id']}"
        with engine.begin() as connection:
            connection.execute(
                update(CanvasRevision.__table__)
                .where(CanvasRevision.canvas_id == canvas_id)
                .values(created_at=utcnow() - timedelta(minutes=6))
            )
        history = owner.get(path + "/revisions").json()["items"]
        response = owner.post(
            path + "/commits",
            headers={"Idempotency-Key": "refperf-binary-foreign-commit"},
            json={"expected_row_version": document["revision"], "source_document": invalid},
        )
        assert response.status_code == 404, response.text
        assert owner.get(path + "/my-document").json()["source_document"] == document
        assert owner.get(path + "/revisions").json()["items"] == history
        assert database_errors == []
    finally:
        owner.close()
        member.close()
        outsider.close()
