"""绘图 DDL 可从前版库补齐，重复执行不修改既有表。"""

import pytest
from sqlalchemy import text

from scripts.canvas_migration import apply, inspect_schema

pytestmark = pytest.mark.integration


def test_drawing_migration_is_additive_and_reentrant(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        for name in (
            "canvas_revision_drawing_references",
            "canvas_drawing_media_references",
            "canvas_drawing_versions",
            "canvas_drawings",
        ):
            connection.execute(text(f"DROP TABLE {name}"))
        before = inspect_schema(connection)
    assert before["changed"] == []
    result = apply(engine)
    assert result["status"] == "ready"
    assert set(result["executed"]) == {
        "table:canvas_drawings",
        "table:canvas_drawing_versions",
        "table:canvas_drawing_media_references",
        "table:canvas_revision_drawing_references",
        "index:canvas_drawings.idx_canvas_drawing_active",
    }
    assert apply(engine)["executed"] == []


def test_drawing_history_migration_from_saved_drawing_schema(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE canvas_revision_drawing_references"))
        before = inspect_schema(connection)
    assert before["missing"] == ["table:canvas_revision_drawing_references"]
    assert before["changed"] == []
    result = apply(engine)
    assert result["status"] == "ready"
    assert result["executed"] == ["table:canvas_revision_drawing_references"]
    assert apply(engine)["executed"] == []
