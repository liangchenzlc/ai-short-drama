"""Creation staging DDL is additive, repeatable and rejects unknown drift."""

import pytest
from sqlalchemy import text

from scripts.canvas_migration import apply, inspect_schema

pytestmark = pytest.mark.integration


def test_creation_tables_upgrade_is_reentrant(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE canvas_creation_resources"))
        connection.execute(text("DROP TABLE canvas_creation_attempts"))
        before = inspect_schema(connection)
    assert before["changed"] == []
    assert "table:canvas_creation_attempts" in before["missing"]
    assert "table:canvas_creation_resources" in before["missing"]
    result = apply(engine)
    assert result["status"] == "ready" and result["changed"] == result["missing"] == []
    assert set(result["executed"]) == {
        "table:canvas_creation_attempts",
        "table:canvas_creation_resources",
        "index:canvas_creation_attempts.idx_canvas_creation_expiry",
    }
    assert apply(engine)["executed"] == []


def test_creation_migration_rejects_unknown_constraint(migration_mysql_engine):
    with migration_mysql_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE canvas_creation_resources DROP CHECK ck_canvas_creation_resource_ids, "
                "ADD CONSTRAINT ck_canvas_creation_resource_ids CHECK (source_resource_id > 0)"
            )
        )
    with pytest.raises(RuntimeError, match="停止新增"):
        apply(migration_mysql_engine)
