"""历史创建媒体删除出处的真实旧库升级、数据保留与漂移拒绝。"""

import re

import pytest
from sqlalchemy import inspect, text

from scripts.canvas_migration import (
    CREATION_DELETE_SQL_PATH,
    CREATION_DELETE_UPGRADE,
    SQL_PATH,
    apply,
    inspect_schema,
)
from short_drama.db.readiness import _normalize_sql
from short_drama.domain import Base
from short_drama.service.base import utcnow

pytestmark = pytest.mark.integration


def test_generated_core_sql_creates_the_current_deletion_table(migration_mysql_engine):
    engine = migration_mysql_engine
    source = re.sub(r"(?m)^\s*--.*$", "", SQL_PATH.read_text(encoding="utf-8"))
    statements = [
        statement.strip()
        for statement in source.split(";")
        if re.match(
            r"CREATE TABLE canvas_resource_deletions\b|"
            r"CREATE (?:UNIQUE )?INDEX \w+ ON canvas_resource_deletions\b",
            statement.strip(),
        )
    ]
    assert len(statements) == 3
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE canvas_resource_deletions"))
        for statement in statements:
            connection.execute(text(statement))
        verified = inspect_schema(connection)
    assert verified["status"] == "ready" and verified["missing"] == verified["changed"] == []
    assert apply(engine)["executed"] == []


def legacy(engine):
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE canvas_resource_deletions "
                "DROP FOREIGN KEY fk_canvas_deletion_creation_resource, "
                "DROP FOREIGN KEY fk_canvas_deletion_disposal, "
                "DROP INDEX uk_canvas_deletion_creation_resource, "
                "DROP INDEX idx_canvas_deletion_disposal, "
                "DROP CHECK ck_canvas_deletion_origin, "
                "DROP COLUMN creation_resource_id, DROP COLUMN disposal_receipt_id, "
                "MODIFY COLUMN upload_id BIGINT UNSIGNED NOT NULL, "
                "MODIFY COLUMN idempotency_hash CHAR(64) CHARACTER SET ascii "
                "COLLATE ascii_bin NOT NULL, "
                "MODIFY COLUMN request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL"
            )
        )


def seed(engine):
    now = utcnow()
    with engine.begin() as connection:
        for index, status in enumerate(("pending", "completed", "retained"), 1):
            connection.execute(
                Base.metadata.tables["canvas_resource_deletions"]
                .insert()
                .values(
                    id=index + 10,
                    user_id=1,
                    resource_id=index + 20,
                    upload_id=index + 30,
                    idempotency_hash=str(index) * 64,
                    request_hash=str(index + 3) * 64,
                    storage_locator=f"minio://migration-test/canvas/resources/{index + 20}",
                    chunk_count=index,
                    status=status,
                    attempts=index,
                    next_attempt_at=now,
                    completed_at=None if status == "pending" else now,
                    error_code="old_failure" if status == "pending" else None,
                    created_at=now,
                    updated_at=now,
                    created_by=1,
                    updated_by=1,
                )
            )


def snapshot(connection):
    return [
        dict(row)
        for row in connection.execute(
            text("SELECT * FROM canvas_resource_deletions ORDER BY id")
        ).mappings()
    ]


@pytest.mark.parametrize("explicit_sql", [False, True])
def test_populated_upload_outbox_is_preserved_by_exact_creation_upgrade(
    migration_mysql_engine, explicit_sql
):
    engine = migration_mysql_engine
    seed(engine)
    legacy(engine)
    with engine.connect() as connection:
        before = inspect_schema(connection)
        rows = snapshot(connection)
    assert before["changed"] == []
    assert before["missing"] == ["upgrade:" + CREATION_DELETE_UPGRADE]
    if explicit_sql:
        source = re.sub(r"(?m)^\s*--.*$", "", CREATION_DELETE_SQL_PATH.read_text(encoding="utf-8"))
        with engine.begin() as connection:
            for statement in source.split(";"):
                if statement.strip():
                    connection.execute(text(statement))
    else:
        assert apply(engine)["executed"] == ["upgrade:" + CREATION_DELETE_UPGRADE]
    with engine.connect() as connection:
        state = inspect_schema(connection)
        upgraded = snapshot(connection)
    assert state["status"] == "ready" and state["changed"] == state["missing"] == []
    for row in upgraded:
        assert row.pop("creation_resource_id") is None
        assert row.pop("disposal_receipt_id") is None
    assert upgraded == rows
    assert apply(engine)["executed"] == []


def test_fresh_creation_deletion_check_matches_its_reflected_mysql_definition(
    migration_mysql_engine,
):
    expected = next(
        item
        for item in Base.metadata.tables["canvas_resource_deletions"].constraints
        if item.name == "ck_canvas_deletion_origin"
    )
    with migration_mysql_engine.connect() as connection:
        actual = next(
            item
            for item in inspect(connection).get_check_constraints("canvas_resource_deletions")
            if item["name"] == expected.name
        )
        state = inspect_schema(connection)
    assert _normalize_sql(actual["sqltext"]) == _normalize_sql(expected.sqltext)
    assert state["status"] == "ready"


@pytest.mark.parametrize(
    "change",
    [
        "nullable_upload",
        "changed_hash",
        "partial_origin",
        "changed_status",
        "unenforced_completion",
    ],
)
def test_unknown_creation_deletion_drift_is_refused_before_any_upgrade(
    migration_mysql_engine, change
):
    engine = migration_mysql_engine
    legacy(engine)
    sql = {
        "nullable_upload": "MODIFY COLUMN upload_id BIGINT UNSIGNED NULL",
        "changed_hash": "MODIFY COLUMN request_hash CHAR(63) CHARACTER SET ascii "
        "COLLATE ascii_bin NOT NULL",
        "partial_origin": "ADD COLUMN creation_resource_id BIGINT UNSIGNED NULL",
        "changed_status": "DROP CHECK ck_canvas_deletion_status, "
        "ADD CONSTRAINT ck_canvas_deletion_status CHECK (status = 'pending')",
        "unenforced_completion": "ALTER CHECK ck_canvas_deletion_completion NOT ENFORCED",
    }[change]
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE canvas_resource_deletions " + sql))
        before = inspect_schema(connection)
    assert before["changed"]
    with pytest.raises(RuntimeError, match="结构与 ORM 不符"):
        apply(engine)
    with engine.connect() as connection:
        assert inspect_schema(connection) == before
