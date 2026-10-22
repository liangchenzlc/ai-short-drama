"""审美执行两表的真实增量升级、重入和结构漂移拒绝。"""

import pytest
from sqlalchemy import text

from scripts.canvas_migration import STORYBOARD_UPGRADE, apply, inspect_schema

pytestmark = pytest.mark.integration


def test_tool_run_migration_is_additive_and_reentrant(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE canvas_tool_submissions"))
        connection.execute(text("DROP TABLE canvas_tool_runs"))
        before = inspect_schema(connection)
    assert before["changed"] == []
    assert before["missing"] == ["table:canvas_tool_runs", "table:canvas_tool_submissions"]
    result = apply(engine)
    assert result["status"] == "ready"
    assert result["executed"] == [
        "table:canvas_tool_runs",
        "index:canvas_tool_runs.idx_canvas_tool_run_author",
        "table:canvas_tool_submissions",
    ]
    assert apply(engine)["executed"] == []
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE canvas_tool_runs DROP CHECK ck_canvas_tool_run_lease"))
    with pytest.raises(RuntimeError, match="结构与 ORM 不符"):
        apply(engine)


def test_storyboard_source_nullable_upgrade_preserves_foreign_key_and_reenters(
    migration_mysql_engine,
):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE canvas_tool_runs DROP FOREIGN KEY fk_canvas_tool_runs_source_media_id"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE canvas_tool_runs "
                "MODIFY COLUMN source_media_id BIGINT UNSIGNED NOT NULL"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE canvas_tool_runs "
                "ADD CONSTRAINT fk_canvas_tool_runs_source_media_id "
                "FOREIGN KEY (source_media_id) REFERENCES media_files (id) "
                "ON DELETE RESTRICT ON UPDATE RESTRICT"
            )
        )
        before = inspect_schema(connection)
    assert before["changed"] == [] and before["missing"] == ["upgrade:" + STORYBOARD_UPGRADE]
    upgraded = apply(engine)
    assert upgraded["status"] == "ready" and upgraded["executed"] == [
        "upgrade:" + STORYBOARD_UPGRADE
    ]
    assert apply(engine)["executed"] == []
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE canvas_tool_runs MODIFY COLUMN "
                "source_media_id BIGINT UNSIGNED NULL DEFAULT 1"
            )
        )
    with pytest.raises(RuntimeError, match="结构与 ORM 不符"):
        apply(engine)
