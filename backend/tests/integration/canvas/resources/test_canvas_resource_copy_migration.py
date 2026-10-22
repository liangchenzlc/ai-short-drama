"""已知旧 CHECK 可升级，未知漂移必须停止；所有 DDL 限定随机测试库。"""

import pytest
from sqlalchemy import text

from scripts.canvas_migration import apply, inspect_schema

pytestmark = pytest.mark.integration


def test_resource_copy_upgrade_from_prior_upload_schema_is_reentrant(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE canvas_resource_copy_sources"))
        connection.execute(
            text(
                "ALTER TABLE canvas_resource_uploads DROP CHECK ck_canvas_upload_mode, "
                "ADD CONSTRAINT ck_canvas_upload_mode CHECK (mode IN ('multipart','chunked'))"
            )
        )
        before = inspect_schema(connection)
    assert before["changed"] == []
    assert "upgrade:canvas_resource_uploads.copy" in before["missing"]
    assert "table:canvas_resource_copy_sources" in before["missing"]
    result = apply(engine)
    assert result["status"] == "ready" and result["changed"] == result["missing"] == []
    assert set(result["executed"]) == {
        "table:canvas_resource_copy_sources",
        "index:canvas_resource_copy_sources.idx_canvas_copy_origin",
        "upgrade:canvas_resource_uploads.copy",
    }
    assert apply(engine)["executed"] == []


def test_resource_copy_migration_refuses_unknown_check_drift(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE canvas_resource_uploads DROP CHECK ck_canvas_upload_mode, "
                "ADD CONSTRAINT ck_canvas_upload_mode "
                "CHECK (mode IN ('multipart','chunked','unknown'))"
            )
        )
    with pytest.raises(RuntimeError, match="停止新增"):
        apply(engine)
    with engine.connect() as connection:
        assert (
            "canvas_resource_uploads.ck_canvas_upload_mode:check"
            in inspect_schema(connection)["changed"]
        )
