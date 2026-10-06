"""显式新增会话对象范围；按已存在字段、索引和约束安全重入，不猜测旧记录归属。"""

import argparse
import json
import logging
from pathlib import Path

from sqlalchemy import CheckConstraint, inspect, text
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import AddConstraint, CreateColumn, CreateIndex

from short_drama.core.logging import configure_logging
from short_drama.db.readiness import _normalize_sql, _type_signature
from short_drama.domain import Base

logger = logging.getLogger(__name__)
TABLE_NAME = "agent_conversations"
SCOPE_COLUMNS = ("stage", "subject_type", "subject_id", "task_type", "scope_version")
SCOPE_INDEX = "idx_agent_conversations_scope"
SCOPE_CHECK = "ck_agent_conversations_scope"
RUN_TABLE = "agent_runs"
RUN_ACTIVE_INDEX = "idx_agent_runs_active"
LEGACY_RUN_UNIQUE = "uk_agent_runs_active_conversation"
SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "数据库模型"
    / "migrations"
    / "2026-10-04-agent-creation-scope"
    / "001-conversation-scope.sql"
)


def scope_statements():
    table = Base.metadata.tables[TABLE_NAME]
    statements = []
    for name in SCOPE_COLUMNS:
        declaration = str(CreateColumn(table.c[name]).compile(dialect=dialect())).strip()
        statements.append(("column", name, f"ALTER TABLE {TABLE_NAME} ADD COLUMN {declaration}"))
    index = next(value for value in table.indexes if value.name == SCOPE_INDEX)
    statements.append(("index", SCOPE_INDEX, str(CreateIndex(index).compile(dialect=dialect()))))
    check = next(
        value
        for value in table.constraints
        if isinstance(value, CheckConstraint) and value.name == SCOPE_CHECK
    )
    original_rule = check._create_rule
    declaration = str(AddConstraint(check).compile(dialect=dialect()))
    check._create_rule = original_rule
    statements.append(("check", SCOPE_CHECK, declaration))
    run_table = Base.metadata.tables[RUN_TABLE]
    run_index = next(value for value in run_table.indexes if value.name == RUN_ACTIVE_INDEX)
    statements.append(
        ("index", RUN_ACTIVE_INDEX, str(CreateIndex(run_index).compile(dialect=dialect())))
    )
    statements.append(
        ("drop_index", LEGACY_RUN_UNIQUE, f"DROP INDEX {LEGACY_RUN_UNIQUE} ON {RUN_TABLE}")
    )
    return statements


def scope_schema_sql():
    return (
        "-- 会话对象范围增量：旧记录保留 scope_version=0、四个 scope 字段 NULL。\n"
        "-- 从当前 AgentConversation ORM 生成；先选择目标数据库。\n"
        "-- 活动 Run 索引改为非唯一：允许消息排队，制作执行由项目/会话锁串行准入。\n"
        "-- 安全重入使用 scripts/agent_scope_migration.py --apply，勿盲目重复执行本 SQL。\n\n"
        + "\n\n".join(statement.strip() + ";" for _, _, statement in scope_statements())
        + "\n"
    )


def inspect_scope_schema(connection):
    inspector = inspect(connection)
    database = connection.scalar(text("SELECT DATABASE()"))
    missing_tables = {TABLE_NAME, RUN_TABLE} - set(inspector.get_table_names())
    if missing_tables:
        return {
            "database": database,
            "status": "absent",
            "missing": sorted(missing_tables),
            "changed": [],
        }
    table = Base.metadata.tables[TABLE_NAME]
    columns = {value["name"]: value for value in inspector.get_columns(TABLE_NAME)}
    missing, changed = [], []
    for name in SCOPE_COLUMNS:
        actual, expected = columns.get(name), table.c[name]
        if actual is None:
            missing.append(f"column:{name}")
            continue
        default = str(expected.server_default.arg) if expected.server_default else None
        actual_default = actual.get("default")
        if (
            _type_signature(actual["type"]) != _type_signature(expected.type)
            or actual["nullable"] != expected.nullable
            or (default is None) != (actual_default is None)
            or default is not None
            and _normalize_sql(default) != _normalize_sql(actual_default)
        ):
            changed.append(f"column:{name}")
    indexes = {value["name"]: value for value in inspector.get_indexes(TABLE_NAME)}
    index = next(value for value in table.indexes if value.name == SCOPE_INDEX)
    actual_index = indexes.get(SCOPE_INDEX)
    if actual_index is None:
        missing.append(f"index:{SCOPE_INDEX}")
    elif (
        tuple(actual_index["column_names"]) != tuple(value.name for value in index.columns)
        or actual_index["unique"]
    ):
        changed.append(f"index:{SCOPE_INDEX}")
    checks = {value["name"]: value for value in inspector.get_check_constraints(TABLE_NAME)}
    check = next(
        value
        for value in table.constraints
        if isinstance(value, CheckConstraint) and value.name == SCOPE_CHECK
    )
    actual_check = checks.get(SCOPE_CHECK)
    if actual_check is None:
        missing.append(f"check:{SCOPE_CHECK}")
    else:
        enforced = connection.scalar(
            text(
                "SELECT ENFORCED FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND CONSTRAINT_NAME=:name"
            ),
            {"table": TABLE_NAME, "name": SCOPE_CHECK},
        )
        if (
            _normalize_sql(actual_check["sqltext"]) != _normalize_sql(check.sqltext)
            or enforced != "YES"
        ):
            changed.append(f"check:{SCOPE_CHECK}")
    run_indexes = {value["name"]: value for value in inspector.get_indexes(RUN_TABLE)}
    active_index = run_indexes.get(RUN_ACTIVE_INDEX)
    if active_index is None:
        missing.append(f"index:{RUN_ACTIVE_INDEX}")
    elif (
        tuple(active_index["column_names"]) != ("active_conversation_id",) or active_index["unique"]
    ):
        changed.append(f"index:{RUN_ACTIVE_INDEX}")
    legacy_index = run_indexes.get(LEGACY_RUN_UNIQUE)
    if legacy_index is not None:
        if (
            tuple(legacy_index["column_names"]) == ("active_conversation_id",)
            and legacy_index["unique"]
        ):
            missing.append(f"drop_index:{LEGACY_RUN_UNIQUE}")
        else:
            changed.append(f"index:{LEGACY_RUN_UNIQUE}")
    return {
        "database": database,
        "status": "partial" if missing or changed else "ready",
        "missing": sorted(missing),
        "changed": sorted(changed),
    }


def apply(engine):
    with engine.connect() as connection:
        before = inspect_scope_schema(connection)
        if before["status"] == "absent" or before["changed"]:
            raise RuntimeError("会话表缺失或已有范围定义不兼容；请先处理预检差异")
        logger.info("开始会话范围迁移，目标数据库：%s", before["database"])
        for kind, name, statement in scope_statements():
            state = inspect_scope_schema(connection)
            if f"{kind}:{name}" not in state["missing"]:
                continue
            connection.exec_driver_sql(statement)
            connection.commit()
            logger.info("已执行 DDL：%s", statement.strip())
        return inspect_scope_schema(connection)


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--precheck", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--export", action="store_true")
    action.add_argument("--check-export", action="store_true")
    args = parser.parse_args()
    if args.export or args.check_export:
        source = scope_schema_sql()
        if args.check_export:
            if not SQL_PATH.exists() or SQL_PATH.read_text(encoding="utf-8") != source:
                logger.error("会话范围增量 SQL 未同步，请执行 --export")
                return 1
        else:
            SQL_PATH.parent.mkdir(parents=True, exist_ok=True)
            SQL_PATH.write_text(source, encoding="utf-8", newline="\n")
        logger.info("会话范围增量 SQL 已与当前 ORM 对齐")
        return 0
    from short_drama.core.config import Settings
    from short_drama.db.session import build_engine

    engine = build_engine(Settings())
    try:
        if args.apply:
            state = apply(engine)
        else:
            with engine.connect() as connection:
                state = inspect_scope_schema(connection)
        logger.info("会话范围结构：%s", json.dumps(state, ensure_ascii=False))
        return 0 if state["status"] == "ready" else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
