"""018 只在随机隔离 MySQL 运行；候选准备阶段不执行 DDL。"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from scripts.canvas_migration import (
    SOURCE_BINDING_COLUMN,
    SOURCE_BINDING_FK,
    SOURCE_BINDING_FOREIGN,
    SOURCE_BINDING_INDEX,
    apply,
    inspect_schema,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_SOURCE_BINDING") != "1",
        reason="Explicitly enable isolated source binding verification",
    ),
]


def remove_binding(connection, stage):
    if stage < 3:
        connection.execute(
            text(f"ALTER TABLE project_canvases DROP FOREIGN KEY {SOURCE_BINDING_FK}")
        )
    if stage < 2:
        connection.execute(text(f"DROP INDEX {SOURCE_BINDING_INDEX} ON project_canvases"))
    if stage < 1:
        connection.execute(text("ALTER TABLE project_canvases DROP COLUMN source_project_id"))


def seed_legacy_canvas(connection):
    connection.execute(
        text(
            "INSERT INTO projects "
            "(id, owner_user_id, name, aspect, synopsis, style, workspace_mode, "
            "row_version, last_opened_at, created_at, updated_at, created_by, updated_by) "
            "VALUES (2, 1, 'fixture', '16:9', '', '', 'infinite_canvas', 1, "
            "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), 1, 1)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO project_canvases "
            "(id, project_id, source_key, title, position, schema_version, row_version, "
            "properties_json, created_at, updated_at, created_by, updated_by) "
            "VALUES (3, 2, 'fixture', 'fixture', 0, 1, 1, JSON_OBJECT('projectId', '999999'), "
            "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), 1, 1)"
        )
    )


@pytest.mark.parametrize("stage", [0, 1, 2, 3])
def test_source_binding_migration_resumes_only_missing_components(migration_mysql_engine, stage):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        seed_legacy_canvas(connection)
        remove_binding(connection, stage)
        state = inspect_schema(connection)
    assert state["changed"] == []
    expected = set()
    if stage < 1:
        expected.add("upgrade:" + SOURCE_BINDING_COLUMN)
    if stage < 2:
        expected.add("index:project_canvases." + SOURCE_BINDING_INDEX)
    if stage < 3:
        expected.add("upgrade:" + SOURCE_BINDING_FOREIGN)
    assert set(state["missing"]) == expected
    result = apply(engine)
    assert result["status"] == "ready"
    assert set(result["executed"]) == expected
    assert apply(engine)["executed"] == []
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM project_canvases WHERE source_project_id IS NOT NULL")
            )
            == 0
        )
        assert connection.scalar(text("SELECT COUNT(*) FROM project_canvases")) == 1
        assert (
            connection.scalar(
                text("SELECT JSON_UNQUOTE(properties_json->'$.projectId') FROM project_canvases")
            )
            == "999999"
        )


@pytest.mark.parametrize(
    "ddl",
    [
        "MODIFY COLUMN source_project_id BIGINT NULL",
        "MODIFY COLUMN source_project_id BIGINT UNSIGNED NOT NULL",
        "MODIFY COLUMN source_project_id BIGINT UNSIGNED NULL DEFAULT 1",
        "ADD CONSTRAINT fk_canvas_source_project FOREIGN KEY(source_project_id) "
        "REFERENCES users(id) ON DELETE RESTRICT ON UPDATE RESTRICT",
        "ADD CONSTRAINT fk_unexpected_source FOREIGN KEY(source_project_id) "
        "REFERENCES projects(id) ON DELETE RESTRICT ON UPDATE RESTRICT",
    ],
)
def test_source_binding_migration_rejects_unknown_drift(migration_mysql_engine, ddl):
    with migration_mysql_engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE project_canvases DROP FOREIGN KEY {SOURCE_BINDING_FK}")
        )
        connection.execute(text("ALTER TABLE project_canvases " + ddl))
        assert inspect_schema(connection)["changed"]
    with pytest.raises(RuntimeError, match="停止新增"):
        apply(migration_mysql_engine)


def test_source_binding_migration_rejects_wrong_index(migration_mysql_engine):
    with migration_mysql_engine.begin() as connection:
        remove_binding(connection, 1)
        connection.execute(
            text(f"CREATE INDEX {SOURCE_BINDING_INDEX} ON project_canvases (project_id)")
        )
        assert inspect_schema(connection)["changed"]
    with pytest.raises(RuntimeError, match="停止新增"):
        apply(migration_mysql_engine)


def test_source_binding_foreign_key_rejects_missing_project(migration_mysql_engine):
    # A real foreign-key check, not a metadata-only comparison.
    with migration_mysql_engine.begin() as connection:
        seed_legacy_canvas(connection)
        with pytest.raises(IntegrityError):
            connection.execute(
                text("UPDATE project_canvases SET source_project_id = 999999 WHERE id = 3")
            )
