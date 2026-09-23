"""Verify the latest incremental migration only in the disposable MySQL fixture."""

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from test_production_workflow_migration import contract_snapshot, mysql_statements

pytestmark = pytest.mark.integration
MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "docs/数据库模型/migrations/2026-09-22-storyboard-prompts"
)


def migrate(connection):
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for statement in mysql_statements(path):
            connection.exec_driver_sql(statement)


def test_storyboard_prompt_migration_preserves_data_and_matches_canonical(migration_mysql_engine):
    with migration_mysql_engine.connect() as connection:
        database = connection.scalar(text("SELECT DATABASE()"))
        assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database)
        canonical = contract_snapshot(connection)
        connection.exec_driver_sql(
            "INSERT INTO projects (id,name,aspect) VALUES (9101,'migration','16:9')"
        )
        connection.exec_driver_sql(
            "INSERT INTO episodes (id,project_id,position,title,aspect) "
            "VALUES (9102,9101,1,'episode','16:9')"
        )
        connection.exec_driver_sql(
            "INSERT INTO shot_scripts (id,episode_id,position,script,row_version) "
            "VALUES (9103,9102,1,'  legacy script  ',7)"
        )
        connection.commit()
        connection.exec_driver_sql(
            "ALTER TABLE shot_scripts DROP CHECK ck_shot_scripts_duration_ms, "
            "DROP COLUMN source_excerpt, DROP COLUMN duration_ms"
        )
        statement = text("SELECT * FROM shot_scripts WHERE id=9103")
        before = dict(connection.execute(statement).mappings().one())
        migrate(connection)
        migrated = dict(connection.execute(statement).mappings().one())
        assert {key: migrated[key] for key in before} == before
        assert migrated["duration_ms"] == 3000
        assert migrated["source_excerpt"] == ""
        assert contract_snapshot(connection) == canonical

        connection.exec_driver_sql(
            "UPDATE shot_scripts SET duration_ms=7500, source_excerpt='original excerpt', "
            "row_version=8 WHERE id=9103"
        )
        connection.commit()
        changed = dict(connection.execute(statement).mappings().one())
        migrate(connection)
        rerun = dict(connection.execute(statement).mappings().one())
        assert rerun == changed
        assert contract_snapshot(connection) == canonical

    for duration in (999, 10001):
        with pytest.raises(OperationalError) as error, migration_mysql_engine.begin() as connection:
            connection.execute(
                text("UPDATE shot_scripts SET duration_ms=:duration WHERE id=9103"),
                {"duration": duration},
            )
        assert error.value.orig.args[0] == 3819
