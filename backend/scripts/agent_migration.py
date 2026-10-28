"""Explicit additive Agent migration; never called by application startup.

DDL commits implicitly in MySQL. Each complete table can be resumed safely;
existing incompatible definitions are reported instead of silently altered.
"""

import argparse
import json
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from short_drama.db.readiness import inspect_agent_schema, schema_gaps
from short_drama.domain import AGENT_TABLES, Base


def deferred_foreign_keys():
    return sorted(
        (
            constraint
            for table in Base.metadata.tables.values()
            if table.name in AGENT_TABLES
            for constraint in table.foreign_key_constraints
            if constraint.use_alter
        ),
        key=lambda item: (item.table.name, item.name),
    )


def add_constraint_sql(constraint):
    original_rule = constraint._create_rule
    try:
        return str(AddConstraint(constraint).compile(dialect=dialect())).strip()
    finally:
        constraint._create_rule = original_rule


def agent_schema_sql():
    statements = []
    for table in Base.metadata.sorted_tables:
        if table.name not in AGENT_TABLES:
            continue
        statements.append(str(CreateTable(table).compile(dialect=dialect())).strip() + ";")
        statements.extend(
            str(CreateIndex(index).compile(dialect=dialect())).strip() + ";"
            for index in sorted(table.indexes, key=lambda value: value.name)
        )
    statements.extend(
        add_constraint_sql(constraint) + ";" for constraint in deferred_foreign_keys()
    )
    source = (
        f"-- Additive Agent schema: {len(AGENT_TABLES)} tables; preserves existing columns/data, "
        "adds missing named deferred foreign keys.\n"
        "-- Generated from the current Agent domain models; "
        "select the intended database first.\n"
        "-- Use scripts/agent_migration.py --apply for safe table-level reentry.\n\n"
        + "\n\n".join(statements)
        + "\n"
    )
    return "\n".join(line.rstrip() for line in source.splitlines()) + "\n"


def apply(engine):
    with engine.connect() as connection:
        missing = schema_gaps(connection)
    if missing:
        raise RuntimeError("Base schema incomplete. Apply existing migrations before Agent tables.")
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name in AGENT_TABLES:
                table.create(connection, checkfirst=True)
        for constraint in deferred_foreign_keys():
            if not any(
                item["name"] == constraint.name
                for item in inspect(connection).get_foreign_keys(constraint.table.name)
            ):
                connection.exec_driver_sql(add_constraint_sql(constraint))
    with engine.connect() as connection:
        state = inspect_agent_schema(connection)
    if state["status"] != "ready":
        raise RuntimeError(
            "Existing Agent definitions do not match this migration; inspect --precheck gaps. "
            "No existing columns or records were altered."
        )
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--precheck", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--export", action="store_true")
    action.add_argument("--check-export", action="store_true")
    args = parser.parse_args()
    path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "database"
        / "migrations"
        / "2026-10-02-agent-mode"
        / "001-agent-tables.sql"
    )
    if args.export or args.check_export:
        source = agent_schema_sql()
        if args.check_export:
            if not path.exists() or path.read_text(encoding="utf-8") != source:
                print("Agent migration SQL is out of date. Run --export.")
                return 1
        else:
            path.write_text(source, encoding="utf-8", newline="\n")
        print(f"Agent migration SQL matches {len(AGENT_TABLES)} current models.")
        return 0
    from short_drama.core.config import Settings
    from short_drama.db.session import build_engine

    engine = build_engine(Settings())
    try:
        if args.apply:
            state = apply(engine)
        else:
            with engine.connect() as connection:
                state = inspect_agent_schema(connection)
        print(json.dumps(state, ensure_ascii=False))
        return 0 if state["status"] == "ready" else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
