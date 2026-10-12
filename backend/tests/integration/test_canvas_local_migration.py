"""本地任务表的实际旧库升级、重入与结构漂移拒绝。"""

import pytest
from sqlalchemy import text

from scripts.canvas_migration import apply, inspect_schema

pytestmark = pytest.mark.integration


def test_local_job_migration_is_additive_and_reentrant(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE canvas_local_jobs"))
        before = inspect_schema(connection)
    assert before["changed"] == [] and before["missing"] == ["table:canvas_local_jobs"]
    result = apply(engine)
    assert result["status"] == "ready"
    assert result["executed"] == [
        "table:canvas_local_jobs",
        "index:canvas_local_jobs.idx_canvas_local_author",
        "index:canvas_local_jobs.idx_canvas_local_disposal",
        "index:canvas_local_jobs.idx_canvas_local_schedule",
    ]
    assert apply(engine)["executed"] == []
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE canvas_local_jobs DROP CHECK ck_canvas_local_lease"))
    with pytest.raises(RuntimeError, match="结构与 ORM 不符"):
        apply(engine)
