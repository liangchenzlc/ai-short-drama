"""Explicit additive Agent migration; never called by application startup.

DDL commits implicitly in MySQL. Each complete table can be resumed safely;
existing incompatible definitions are reported instead of silently altered.
"""

import argparse
import json
from pathlib import Path

from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from short_drama.db.readiness import inspect_agent_schema, schema_gaps
from short_drama.domain import AGENT_TABLES, Base


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
    source = (
        f"-- Additive Agent schema: {len(AGENT_TABLES)} tables, no existing-table alterations.\n"
        "-- Generated from short_drama.domain.agent and agent_context; "
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
        / "数据库模型"
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
