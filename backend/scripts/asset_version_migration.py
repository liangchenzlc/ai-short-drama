"""显式迁移角色版本结构；默认只读预检，不回填内容、不建立项目来源。"""

import argparse
import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import AddConstraint, CreateColumn, CreateIndex, CreateTable

from short_drama.core.config import Settings
from short_drama.core.logging import configure_logging
from short_drama.db.readiness import _normalize_sql, _type_signature
from short_drama.db.session import build_engine
from short_drama.domain import Base

logger = logging.getLogger(__name__)
TABLES = ("asset_versions", "asset_version_media_references")
POINTER_COLUMN = "current_version_id"
POINTER_INDEX = "idx_assets_current_version"
POINTER_FK = "fk_assets_current_version"
POINTER_CHECK = "ck_assets_character_version"
SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/database/migrations/2026-10-05-infinite-canvas/019-asset-versions.sql"
)


def constraint_sql(constraint) -> str:
    """AddConstraint 的编译标记不得改变后续 full SQL 的约束输出。"""
    original_rule = constraint._create_rule
    try:
        return str(AddConstraint(constraint).compile(dialect=dialect()))
    finally:
        constraint._create_rule = original_rule


def statements() -> list[tuple[str, str, str]]:
    result = []
    for name in TABLES:
        table = Base.metadata.tables[name]
        result.append(("table", name, str(CreateTable(table).compile(dialect=dialect())).strip()))
        for column in table.columns:
            result.append(
                (
                    "column",
                    f"{name}.{column.name}",
                    f"ALTER TABLE {name} ADD COLUMN "
                    + str(CreateColumn(column).compile(dialect=dialect())),
                )
            )
        for index in sorted(table.indexes, key=lambda item: item.name):
            result.append(
                (
                    "index",
                    f"{name}.{index.name}",
                    str(CreateIndex(index).compile(dialect=dialect())),
                )
            )
        for constraint in sorted(table.constraints, key=lambda item: item.name or ""):
            if constraint.name:
                result.append(
                    ("constraint", f"{name}.{constraint.name}", constraint_sql(constraint))
                )
    assets = Base.metadata.tables["assets"]
    result.append(
        (
            "column",
            f"assets.{POINTER_COLUMN}",
            "ALTER TABLE assets ADD COLUMN "
            + str(CreateColumn(assets.c[POINTER_COLUMN]).compile(dialect=dialect())),
        )
    )
    index = next(item for item in assets.indexes if item.name == POINTER_INDEX)
    result.append(
        ("index", f"assets.{POINTER_INDEX}", str(CreateIndex(index).compile(dialect=dialect())))
    )
    for name in (POINTER_CHECK, POINTER_FK):
        constraint = next(item for item in assets.constraints if item.name == name)
        result.append(("constraint", f"assets.{name}", constraint_sql(constraint)))
    return result


def _foreign_signature(foreign) -> tuple:
    return (
        tuple(column.name for column in foreign.columns),
        foreign.referred_table.name,
        tuple(element.column.name for element in foreign.elements),
        foreign.ondelete or "RESTRICT",
        foreign.onupdate or "RESTRICT",
    )


def _default_signature(value) -> str:
    normalized = _normalize_sql(value)
    return "null" if normalized in {"none", "null"} else normalized


def _inspect_table(connection, inspector, name: str, missing: list, changed: list) -> None:
    expected = Base.metadata.tables[name]
    columns = {row["name"]: row for row in inspector.get_columns(name)}
    protected = {POINTER_COLUMN} if name == "assets" else set(expected.c.keys())
    if name != "assets" and set(columns) - protected:
        changed.append(f"{name}:extra_columns")
    for key in sorted(protected):
        actual, column = columns.get(key), expected.c[key]
        if actual is None:
            missing.append(f"column:{name}.{key}")
            continue
        default = column.server_default.arg if column.server_default is not None else None
        if (
            _type_signature(actual["type"]) != _type_signature(column.type)
            or actual["nullable"] != column.nullable
            or _default_signature(actual.get("default")) != _default_signature(default)
        ):
            changed.append(f"{name}.{key}:column")
        if actual.get("autoincrement") and column.autoincrement is False:
            changed.append(f"{name}.{key}:autoincrement")
    if name != "assets":
        if tuple(inspector.get_pk_constraint(name).get("constrained_columns") or ()) != ("id",):
            changed.append(f"{name}:primary_key")
        if protected - set(columns) and connection.scalar(text(f"SELECT 1 FROM {name} LIMIT 1")):
            changed.append(f"{name}:nonempty_incomplete_columns")

    indexes = {
        row["name"]: (tuple(row["column_names"]), bool(row["unique"]))
        for row in inspector.get_indexes(name)
    }
    required = {
        index.name: (tuple(column.name for column in index.columns), bool(index.unique))
        for index in expected.indexes
        if name != "assets" or index.name == POINTER_INDEX
    }
    if name != "assets":
        required.update(
            {
                constraint.name: (tuple(column.name for column in constraint.columns), True)
                for constraint in expected.constraints
                if isinstance(constraint, UniqueConstraint)
            }
        )
        implicit_foreign_indexes = {
            foreign.name: (tuple(column.name for column in foreign.columns), False)
            for foreign in expected.foreign_key_constraints
        }
        if any(
            indexes[key] != implicit_foreign_indexes.get(key)
            for key in set(indexes) - set(required)
        ):
            changed.append(f"{name}:extra_indexes")
    elif any(
        POINTER_COLUMN in signature[0] and key != POINTER_INDEX
        for key, signature in indexes.items()
    ):
        changed.append("assets:extra_pointer_indexes")
    for key, signature in required.items():
        if key not in indexes:
            kind = "index" if key in {item.name for item in expected.indexes} else "constraint"
            missing.append(f"{kind}:{name}.{key}")
        elif indexes[key] != signature:
            changed.append(f"{name}.{key}:index")

    checks = {
        row["name"]: _normalize_sql(row["sqltext"]) for row in inspector.get_check_constraints(name)
    }
    enforced = set(
        connection.scalars(
            text(
                "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table "
                "AND CONSTRAINT_TYPE = 'CHECK' AND ENFORCED = 'YES'"
            ),
            {"table": name},
        )
    )
    required_checks = {
        item.name: _normalize_sql(item.sqltext)
        for item in expected.constraints
        if isinstance(item, CheckConstraint) and (name != "assets" or item.name == POINTER_CHECK)
    }
    if name != "assets" and set(checks) - set(required_checks):
        changed.append(f"{name}:extra_checks")
    elif name == "assets" and any(
        POINTER_COLUMN in expression and key != POINTER_CHECK for key, expression in checks.items()
    ):
        changed.append("assets:extra_pointer_checks")
    for key, expression in required_checks.items():
        if key not in checks:
            missing.append(f"constraint:{name}.{key}")
        elif checks[key] != expression or key not in enforced:
            changed.append(f"{name}.{key}:check")

    actual_foreign = {
        row["name"]: (
            tuple(row["constrained_columns"]),
            row["referred_table"],
            tuple(row["referred_columns"]),
            ((row.get("options") or {}).get("ondelete") or "RESTRICT").upper(),
            ((row.get("options") or {}).get("onupdate") or "RESTRICT").upper(),
        )
        for row in inspector.get_foreign_keys(name)
        if name != "assets" or POINTER_COLUMN in row["constrained_columns"]
    }
    required_foreign = {
        item.name: _foreign_signature(item)
        for item in expected.foreign_key_constraints
        if name != "assets" or item.name == POINTER_FK
    }
    if set(actual_foreign) - set(required_foreign):
        changed.append(f"{name}:extra_foreign_keys")
    for key, signature in required_foreign.items():
        if key not in actual_foreign:
            missing.append(f"constraint:{name}.{key}")
        elif actual_foreign[key] != signature:
            changed.append(f"{name}.{key}:foreign_key")
    if name != "assets":
        options = inspector.get_table_options(name)
        if (
            options.get("mysql_engine", "").lower() != "innodb"
            or options.get("mysql_collate") != "utf8mb4_0900_ai_ci"
            or options.get("mysql_row_format", "").upper() != "DYNAMIC"
        ):
            changed.append(f"{name}:storage_options")


def inspect_schema(connection) -> dict:
    """逐项识别已知缺件；存在但不符合合同的结构不能以 IF NOT EXISTS 掩盖。"""
    if connection.dialect.name != "mysql":
        raise ValueError("角色版本迁移只支持 MySQL 8.0.21+")
    inspector = inspect(connection)
    present, missing, changed = set(inspector.get_table_names()), [], []
    for prerequisite in ("assets", "media_files"):
        if prerequisite not in present:
            changed.append(f"{prerequisite}:missing_prerequisite")
    for name in TABLES:
        if name not in present:
            missing.append(f"table:{name}")
        else:
            _inspect_table(connection, inspector, name, missing, changed)
    if "assets" in present:
        _inspect_table(connection, inspector, "assets", missing, changed)
    return {
        "database": connection.scalar(text("SELECT DATABASE()")),
        "status": "changed" if changed else "partial" if missing else "ready",
        "missing": sorted(missing),
        "changed": sorted(changed),
    }


def apply(engine) -> dict:
    """DDL 每步 autocommit；仅补缺件，不修改已有列、不关闭外键检查。"""
    executed = []
    with engine.connect() as connection:
        database = str(connection.scalar(text("SELECT DATABASE()")))
        lock_name = (
            "short_drama:asset_versions:" + hashlib.sha256(database.encode()).hexdigest()[:32]
        )
        if connection.scalar(text("SELECT GET_LOCK(:name, 10)"), {"name": lock_name}) != 1:
            raise RuntimeError("另一个角色版本结构迁移正在运行")
        try:
            state = inspect_schema(connection)
            if state["changed"]:
                raise RuntimeError("角色版本结构漂移，停止迁移：" + json.dumps(state["changed"]))
            for kind, target, sql in statements():
                # Fresh inspection after CREATE prevents duplicating its inline constraints.
                state = inspect_schema(connection)
                if state["changed"]:
                    raise RuntimeError(
                        "DDL 中途结构漂移，停止迁移：" + json.dumps(state["changed"])
                    )
                if f"{kind}:{target}" not in state["missing"]:
                    continue
                connection.execute(text(sql))
                connection.commit()
                executed.append(f"{kind}:{target}")
            state = inspect_schema(connection)
            if state["status"] != "ready":
                raise RuntimeError("角色版本 DDL 后核验失败：" + json.dumps(state))
            return {**state, "executed": executed}
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
            connection.commit()


def write_sql(path: Path = SQL_PATH) -> None:
    """生成仅适用于全缺旧结构的 SQL；部分安装必须用 --apply 精确补缺。"""
    selected = []
    for kind, target, sql in statements():
        if kind == "table" or kind == "index" or target.startswith("assets."):
            selected.append(sql + ";")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "-- 角色不可变版本结构；不回填角色内容/项目来源，不构造历史或声音绑定。\n"
        "-- 仅全缺结构可直接执行；部分完成或重复执行用 asset_version_migration.py --apply。\n"
        + "\n\n".join(selected)
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--write-sql", action="store_true")
    args = parser.parse_args()
    configure_logging()
    if args.write_sql:
        write_sql()
        logger.info("角色版本增量 SQL 已更新")
        return
    engine = build_engine(Settings())
    try:
        if args.apply:
            state = apply(engine)
        else:
            with engine.connect() as connection:
                state = inspect_schema(connection)
        logger.info("角色版本结构检查：%s", json.dumps(state, ensure_ascii=False))
        if args.verify and state["status"] != "ready":
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
