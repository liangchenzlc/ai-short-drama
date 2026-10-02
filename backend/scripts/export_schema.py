"""Export or check the complete, create-only MySQL schema from current models."""

import argparse
import re
from pathlib import Path

from sqlalchemy import PrimaryKeyConstraint
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import AddConstraint, CreateTable

from short_drama.domain import Base


def schema_sql():
    statements = []
    for table in Base.metadata.sorted_tables:
        compiled = str(CreateTable(table).compile(dialect=dialect())).strip()
        options = compiled.split("\n)", 1)[1].strip()
        declarations = []
        for column in table.columns:
            kind = str(column.type.compile(dialect=dialect()))
            kind = re.sub(r"^INTEGER\b", "INT", kind)
            value = f"  `{column.name}` {kind}"
            if column.computed is not None:
                value += f" GENERATED ALWAYS AS ({column.computed.sqltext}) STORED"
            value += " NULL" if column.nullable else " NOT NULL"
            if column.server_default is not None and column.computed is None:
                value += f" DEFAULT {column.server_default.arg}"
            if column.comment:
                value += " COMMENT '" + column.comment.replace("'", "''") + "'"
            declarations.append(value)
        for constraint in sorted(table.constraints, key=lambda item: item.name or ""):
            if isinstance(constraint, PrimaryKeyConstraint):
                declarations.append(
                    "  PRIMARY KEY (" + ", ".join(f"`{c.name}`" for c in constraint.columns) + ")"
                )
            else:
                original_rule = constraint._create_rule
                value = str(AddConstraint(constraint).compile(dialect=dialect())).split(" ADD ", 1)[
                    1
                ]
                constraint._create_rule = original_rule
                value = value.replace("%%", "%")
                value = value.replace("FOREIGN KEY(", "FOREIGN KEY (")
                value = re.sub(r"CONSTRAINT (\w+) UNIQUE \(", r"UNIQUE KEY `\1` (", value)
                declarations.append("  " + value)
        for index in sorted(table.indexes, key=lambda item: item.name):
            fields = ", ".join(f"`{c.name}`" for c in index.columns)
            declarations.append(
                f"  {'UNIQUE ' if index.unique else ''}KEY `{index.name}` ({fields})"
            )
        source = f"CREATE TABLE `{table.name}` (\n" + ",\n".join(declarations) + "\n) " + options
        statements.append(source + ";")
    return (
        f"-- Canonical MySQL 8 schema: {len(statements)} tables.\n"
        "-- Generated from short_drama.domain by backend/scripts/export_schema.py.\n"
        "-- Initialize an empty database; upgrade existing databases with versioned migrations.\n"
        "-- Collaboration upgrades require explicit historical ownership backfill and finalize.\n\n"
        + "\n\n".join(statements)
        + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify the canonical SQL matches current models"
    )
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[2] / "docs" / "数据库模型" / "schema.mysql8.sql"
    source = schema_sql()
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != source:
            print("Canonical SQL is out of date. Run python scripts/export_schema.py.")
            return 1
        print(f"Canonical SQL matches current models: {len(Base.metadata.tables)} tables.")
        return 0
    path.write_text(source, encoding="utf-8", newline="\n")
    print(f"Canonical SQL updated: {len(Base.metadata.tables)} tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
