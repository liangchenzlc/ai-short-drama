"""任务及正文增量仅新增所缺表，并实际验证幂等升级与结构漂移拒绝。"""

import pytest
from sqlalchemy import text

from scripts.canvas_migration import apply, inspect_schema

pytestmark = pytest.mark.integration


def test_task_binding_migration_is_additive_and_reentrant(migration_mysql_engine):
    engine = migration_mysql_engine
    names = [
        "canvas_task_text_deltas",
        "canvas_results",
        "canvas_task_media_references",
        "canvas_task_bindings",
    ]
    with engine.begin() as connection:
        for name in names:
            connection.execute(text(f"DROP TABLE {name}"))
        state = inspect_schema(connection)
    assert state["changed"] == []
    assert state["missing"] == sorted(f"table:{name}" for name in names)
    result = apply(engine)
    assert result["status"] == "ready"
    assert {item for item in result["executed"] if item.startswith("table:")} == {
        f"table:{name}" for name in names
    }
    assert apply(engine)["executed"] == []
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE canvas_results DROP CHECK ck_canvas_result_attachment")
        )
    with pytest.raises(RuntimeError, match="结构与 ORM 不符"):
        apply(engine)
