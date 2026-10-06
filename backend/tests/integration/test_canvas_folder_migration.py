"""项目文件夹升级只新增两表，旧库可重入且严格核验外键与索引。"""

import pytest
from sqlalchemy import text

from scripts.canvas_migration import apply, inspect_schema

pytestmark = pytest.mark.integration


def test_folder_migration_is_additive_and_reentrant(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE canvas_project_folder_items"))
        connection.execute(text("DROP TABLE canvas_project_folders"))
        before = inspect_schema(connection)
    assert before["changed"] == []
    assert before["missing"] == [
        "table:canvas_project_folder_items",
        "table:canvas_project_folders",
    ]
    result = apply(engine)
    assert result["status"] == "ready"
    assert set(result["executed"]) == {
        "table:canvas_project_folders",
        "table:canvas_project_folder_items",
        "index:canvas_project_folders.idx_canvas_project_folder_list",
        "index:canvas_project_folder_items.idx_canvas_project_folder_items",
    }
    assert apply(engine)["executed"] == []
