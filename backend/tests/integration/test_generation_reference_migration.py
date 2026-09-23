"""Validate reference-image DDL and data preservation in a disposable MySQL schema."""

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from test_production_workflow_migration import contract_snapshot, mysql_statements

pytestmark = pytest.mark.integration
MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "docs/数据库模型/migrations/2026-09-24-generation-references/001_add_reference_images.sql"
)


def test_reference_migration_preserves_existing_rows_and_matches_canonical(migration_mysql_engine):
    with migration_mysql_engine.connect() as connection:
        assert re.fullmatch(
            r"short_drama_[a-f0-9]{32}_test", connection.scalar(text("SELECT DATABASE()"))
        )
        canonical = contract_snapshot(connection)
        connection.exec_driver_sql(
            "INSERT INTO projects (id,name,aspect) VALUES (9101,'test','16:9')"
        )
        connection.exec_driver_sql(
            "INSERT INTO episodes (id,project_id,position,title,aspect) "
            "VALUES (9102,9101,1,'episode','16:9')"
        )
        connection.exec_driver_sql(
            "INSERT INTO shot_scripts (id,episode_id,position,script,row_version) "
            "VALUES (9103,9102,1,'existing script',7)"
        )
        connection.exec_driver_sql(
            "INSERT INTO assets (id,kind,name,description,row_version) "
            "VALUES (9104,'character','actor','existing description',4)"
        )
        connection.commit()
        before = {}
        for table in ("assets", "shot_scripts"):
            connection.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN reference_media_ids")
            before[table] = dict(
                connection.execute(text(f"SELECT * FROM {table}")).mappings().one()
            )
        for statement in mysql_statements(MIGRATION):
            connection.exec_driver_sql(statement)
        for table, original in before.items():
            migrated = dict(connection.execute(text(f"SELECT * FROM {table}")).mappings().one())
            assert json.loads(migrated.pop("reference_media_ids")) == []
            assert migrated == original
        assert contract_snapshot(connection) == canonical
