"""显式升级画布风格库与兼容播放，默认仅检查；不改其他业务表。"""

import argparse
import json
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from short_drama.core.config import Settings
from short_drama.db.readiness import _normalize_sql, _type_signature
from short_drama.db.session import build_engine
from short_drama.domain import Base

TABLES = ("canvas_style_profiles", "canvas_style_media_references", "canvas_style_preferences")
LEGACY_CHECKS = {
    "ck_canvas_local_type": "task_type IN ('timeline_transcription','depth_capture')",
    "ck_canvas_local_output": (
        "status <> 'succeeded' OR (result_json IS NOT NULL AND finished_at IS NOT NULL "
        "AND (task_type <> 'depth_capture' OR output_media_id IS NOT NULL "
        "OR disposal_receipt_id IS NOT NULL))"
    ),
}


def create_statements(name: str) -> list[str]:
    table = Base.metadata.tables[name]
    return [str(CreateTable(table).compile(dialect=dialect())).strip()] + [
        str(CreateIndex(index).compile(dialect=dialect()))
        for index in sorted(table.indexes, key=lambda item: item.name)
    ]


def target_checks() -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in Base.metadata.tables["canvas_local_jobs"].constraints
        if constraint.name in LEGACY_CHECKS
    }


def check_statement(name: str, expression: str) -> str:
    return (
        f"ALTER TABLE canvas_local_jobs DROP CHECK {name}, "
        f"ADD CONSTRAINT {name} CHECK ({expression})"
    )


def verify_table(inspector, name: str) -> None:
    expected = Base.metadata.tables[name]
    actual = {column["name"]: column for column in inspector.get_columns(name)}
    for column in expected.columns:
        value = actual.get(column.name)
        if (
            value is None
            or value["nullable"] != column.nullable
            or _type_signature(value["type"]) != _type_signature(column.type)
        ):
            raise RuntimeError(f"{name}.{column.name} 结构不匹配，请人工核对，不自动覆盖")
    indexes = {
        index["name"]: (tuple(index["column_names"]), bool(index["unique"]))
        for index in inspector.get_indexes(name)
    }
    required = {
        index.name: (tuple(column.name for column in index.columns), index.unique)
        for index in expected.indexes
    }
    required.update(
        {
            constraint.name: (tuple(column.name for column in constraint.columns), True)
            for constraint in expected.constraints
            if isinstance(constraint, UniqueConstraint)
        }
    )
    if any(indexes.get(key) != signature for key, signature in required.items()):
        raise RuntimeError(f"{name} 索引不匹配，请人工核对")
    checks = {
        check["name"]: _normalize_sql(check["sqltext"])
        for check in inspector.get_check_constraints(name)
    }
    if any(
        checks.get(check.name) != _normalize_sql(check.sqltext)
        for check in expected.constraints
        if isinstance(check, CheckConstraint)
    ):
        raise RuntimeError(f"{name} 检查约束不匹配，请人工核对")
    foreign_keys = {
        (
            tuple(key["constrained_columns"]),
            key["referred_table"],
            tuple(key["referred_columns"]),
            ((key.get("options") or {}).get("ondelete") or "RESTRICT").upper(),
        )
        for key in inspector.get_foreign_keys(name)
    }
    if any(
        (
            tuple(column.name for column in key.columns),
            key.referred_table.name,
            tuple(element.column.name for element in key.elements),
            (key.ondelete or "RESTRICT").upper(),
        )
        not in foreign_keys
        for key in expected.foreign_key_constraints
    ):
        raise RuntimeError(f"{name} 外键不匹配，请人工核对")


def write_sql() -> None:
    root = Path(__file__).resolve().parents[2]
    destination = (
        root / "docs/database/migrations/2026-10-16-canvas-alignment/001-styles-playback.sql"
    )
    statements = [statement for name in TABLES for statement in create_statements(name)]
    statements += [
        check_statement(name, expression) for name, expression in target_checks().items()
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "-- 画布完整风格、本人偏好与兼容播放；执行前备份并检查现有结构。\n\n"
        + ";\n\n".join(statements)
        + ";\n",
        encoding="utf-8",
    )


def migrate(apply: bool) -> dict:
    engine = build_engine(Settings())
    operations = []
    try:
        with engine.connect() as connection:
            database = connection.scalar(text("SELECT DATABASE()"))
            inspector = inspect(connection)
            names = set(inspector.get_table_names())
            if "canvas_local_jobs" not in names:
                raise RuntimeError("缺少 canvas_local_jobs，请先完成基础画布迁移")
            checks = {
                item["name"]: item["sqltext"]
                for item in inspector.get_check_constraints("canvas_local_jobs")
            }
            for name, expression in target_checks().items():
                actual = _normalize_sql(checks.get(name, ""))
                if actual == _normalize_sql(expression):
                    continue
                if actual != _normalize_sql(LEGACY_CHECKS[name]):
                    raise RuntimeError(f"{name} 不是受支持的旧版约束，请人工核对")
                operations.append(check_statement(name, expression))
            for name in TABLES:
                if name not in names:
                    operations += create_statements(name)
                else:
                    verify_table(inspector, name)
            if apply:
                for statement in operations:
                    connection.execute(text(statement))
                connection.commit()
            return {"database": database, "applied": apply, "statements": operations}
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--write-sql", action="store_true")
    args = parser.parse_args()
    if args.write_sql:
        write_sql()
    else:
        print(json.dumps(migrate(args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
