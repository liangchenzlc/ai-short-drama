"""本地处置外键的实际旧库升级、重入与未知漂移拒绝。"""

import re

import pytest
from sqlalchemy import inspect, text

from scripts.canvas_migration import (
    LEGACY_LOCAL_OUTPUT,
    LEGACY_RENDER_OUTPUT,
    LOCAL_SQL_PATH,
    RENDER_SQL_PATH,
    apply,
    inspect_schema,
)
from short_drama.db.readiness import _normalize_sql
from short_drama.domain import Base
from short_drama.service.base import utcnow

pytestmark = pytest.mark.integration


def test_explicit_sql_for_missing_tables_creates_current_schema_without_legacy_alters(
    migration_mysql_engine,
):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        for name in ("canvas_render_media_references", "canvas_render_jobs", "canvas_local_jobs"):
            connection.execute(text("DROP TABLE " + name))
        for path in (LOCAL_SQL_PATH, RENDER_SQL_PATH):
            sql = re.sub(r"(?m)^\s*--.*$", "", path.read_text(encoding="utf-8"))
            for statement in sql.split(";"):
                if statement.strip():
                    connection.execute(text(statement))
        verified = inspect_schema(connection)
    assert verified["status"] == "ready" and verified["missing"] == verified["changed"] == []


@pytest.mark.parametrize("name", ["canvas_local_jobs", "canvas_render_jobs"])
def test_fresh_disposal_check_matches_its_reflected_mysql_definition(migration_mysql_engine, name):
    expected = next(
        item
        for item in Base.metadata.tables[name].constraints
        if item.name == "ck_canvas_" + name.split("_")[1] + "_disposal"
    )
    with migration_mysql_engine.connect() as connection:
        actual = next(
            item
            for item in inspect(connection).get_check_constraints(name)
            if item["name"] == expected.name
        )
    assert _normalize_sql(actual["sqltext"]) == _normalize_sql(expected.sqltext)


def legacy(engine, *, nullable_source=False):
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE canvas_local_jobs DROP FOREIGN KEY fk_canvas_local_disposal, "
                "DROP INDEX idx_canvas_local_disposal, DROP CHECK ck_canvas_local_disposal, "
                "DROP CHECK ck_canvas_local_source, DROP CHECK ck_canvas_local_output, "
                "DROP COLUMN disposal_receipt_id, "
                "DROP FOREIGN KEY fk_canvas_local_source, "
                "MODIFY COLUMN source_media_id BIGINT UNSIGNED "
                + ("NULL, " if nullable_source else "NOT NULL, ")
                + "ADD CONSTRAINT fk_canvas_local_jobs_source_media_id "
                "FOREIGN KEY (source_media_id) REFERENCES media_files (id), "
                "ADD CONSTRAINT ck_canvas_local_output CHECK (" + LEGACY_LOCAL_OUTPUT + ")"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE canvas_render_jobs DROP FOREIGN KEY fk_canvas_render_disposal, "
                "DROP INDEX idx_canvas_render_disposal, DROP CHECK ck_canvas_render_disposal, "
                "DROP CHECK ck_canvas_render_output, DROP COLUMN disposal_receipt_id, "
                "ADD CONSTRAINT ck_canvas_render_output CHECK (" + LEGACY_RENDER_OUTPUT + ")"
            )
        )


def test_exact_legacy_local_and_render_structure_upgrades_and_replays(migration_mysql_engine):
    engine = migration_mysql_engine
    legacy(engine)
    with engine.connect() as connection:
        before = inspect_schema(connection)
    assert before["changed"] == []
    assert before["missing"] == [
        "upgrade:canvas_local_jobs.disposal",
        "upgrade:canvas_render_jobs.disposal",
    ]
    result = apply(engine)
    assert result["status"] == "ready" and result["missing"] == result["changed"] == []
    assert result["executed"] == [
        "upgrade:canvas_local_jobs.disposal",
        "upgrade:canvas_render_jobs.disposal",
    ]
    assert apply(engine)["executed"] == []


def test_populated_legacy_jobs_keep_all_payload_identity_and_media_on_upgrade(
    migration_mysql_engine,
):
    engine, now = migration_mysql_engine, utcnow()
    audit = {"created_at": now, "updated_at": now, "created_by": 1, "updated_by": 1}
    with engine.begin() as connection:
        connection.execute(
            Base.metadata.tables["projects"]
            .insert()
            .values(
                id=10,
                owner_user_id=1,
                name="旧版任务升级",
                aspect="16:9",
                workspace_mode="infinite_canvas",
                **audit,
            )
        )
        connection.execute(
            Base.metadata.tables["project_canvases"]
            .insert()
            .values(
                id=11,
                project_id=10,
                source_key="populated-local-migration",
                title="旧版本任务",
                position=0,
                **audit,
            )
        )
        for identifier in (12, 15, 16):
            connection.execute(
                Base.metadata.tables["media_files"]
                .insert()
                .values(
                    id=identifier,
                    project_id=10,
                    format_code="video/mp4",
                    storage_locator=f"minio://migration-test/media-{identifier}.mp4",
                    byte_size=1024,
                    checksum_sha256="a" * 64,
                    **audit,
                )
            )
        common = {
            **audit,
            "user_id": 1,
            "project_id": 10,
            "canvas_id": 11,
            "canvas_version": 1,
            "status": "succeeded",
            "finished_at": now,
            "next_run_at": now,
            "request_hash": "b" * 64,
            "request_json": {"private": "完整旧请求"},
            "source_snapshot": {"media_id": "12", "checksum": "a" * 64},
            "result_json": {"resourceId": "15", "private": "完整旧结果"},
        }
        connection.execute(
            Base.metadata.tables["canvas_local_jobs"]
            .insert()
            .values(
                **common,
                id=13,
                client_operation_id="populated-local-operation",
                task_type="depth_capture",
                source_media_id=12,
                output_media_id=15,
            )
        )
        connection.execute(
            Base.metadata.tables["canvas_render_jobs"]
            .insert()
            .values(
                **common,
                id=14,
                client_operation_id="populated-render-operation",
                plan_json={"durationMs": 1000, "private": "完整旧计划"},
                output_media_id=16,
            )
        )
    legacy(engine)
    with engine.connect() as connection:
        before = {
            name: dict(connection.execute(text("SELECT * FROM " + name)).mappings().one())
            for name in ("canvas_local_jobs", "canvas_render_jobs")
        }
    assert apply(engine)["status"] == "ready"
    with engine.connect() as connection:
        for name, row in before.items():
            upgraded = dict(connection.execute(text("SELECT * FROM " + name)).mappings().one())
            assert upgraded.pop("disposal_receipt_id") is None
            assert upgraded == row
        assert connection.scalar(text("SELECT COUNT(*) FROM media_files")) == 3
    assert apply(engine)["executed"] == []


@pytest.mark.parametrize("failure", ["nullable_source", "changed_output", "unenforced_output"])
def test_unknown_legacy_drift_is_refused_before_any_upgrade(migration_mysql_engine, failure):
    engine = migration_mysql_engine
    legacy(engine, nullable_source=failure == "nullable_source")
    with engine.begin() as connection:
        if failure == "changed_output":
            connection.execute(
                text(
                    "ALTER TABLE canvas_render_jobs DROP CHECK ck_canvas_render_output, "
                    "ADD CONSTRAINT ck_canvas_render_output CHECK (status <> 'succeeded')"
                )
            )
        elif failure == "unenforced_output":
            connection.execute(
                text(
                    "ALTER TABLE canvas_render_jobs "
                    "ALTER CHECK ck_canvas_render_output NOT ENFORCED"
                )
            )
    with pytest.raises(RuntimeError, match="结构与 ORM 不符"):
        apply(engine)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME IN ('canvas_local_jobs','canvas_render_jobs') "
                    "AND COLUMN_NAME='disposal_receipt_id'"
                )
            )
            == 0
        )
