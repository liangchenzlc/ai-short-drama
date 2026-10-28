"""Add durable script change snapshots without rewriting existing work."""

import argparse
import logging
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateTable

from short_drama.core.config import Settings
from short_drama.db.session import build_engine
from short_drama.domain import ProjectScriptRevision

logger = logging.getLogger(__name__)


def migrate(engine):
    table = ProjectScriptRevision.__table__
    with engine.begin() as connection:
        table.create(connection, checkfirst=True)
        inspector = inspect(connection)
        actual = {
            column["name"]: (
                str(column["type"].compile(dialect=engine.dialect)),
                column["nullable"],
            )
            for column in inspector.get_columns(table.name)
        }
        expected = {
            column.name: (str(column.type.compile(dialect=engine.dialect)), column.nullable)
            for column in table.columns
        }
        unique = {
            item["name"]: item["column_names"]
            for item in inspector.get_unique_constraints(table.name)
        }
        foreign = {
            item["name"]: (
                item["constrained_columns"],
                item["referred_table"],
                item["referred_columns"],
            )
            for item in inspector.get_foreign_keys(table.name)
        }
        checks = {item["name"] for item in inspector.get_check_constraints(table.name)}
        if (
            actual != expected
            or inspector.get_pk_constraint(table.name)["constrained_columns"] != ["id"]
            or unique.get("uk_script_revisions_version") != ["project_id", "after_version"]
            or foreign.get("fk_script_revisions_project") != (["project_id"], "projects", ["id"])
            or foreign.get("fk_script_revisions_script")
            != (["script_id"], "project_scripts", ["id"])
            or "ck_script_revisions_version" not in checks
        ):
            raise RuntimeError(
                "Existing script revision structure differs; inspect before deploying"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = (
        Path(__file__).resolve().parents[2] / "docs/database/migrations/2026-10-27-script-revisions"
    )
    target.mkdir(parents=True, exist_ok=True)
    ddl = str(CreateTable(ProjectScriptRevision.__table__).compile(dialect=dialect())).strip()
    (target / "001_script_revisions.sql").write_text(ddl + ";\n", encoding="utf-8", newline="\n")
    if args.apply:
        engine = build_engine(Settings())
        try:
            migrate(engine)
            logger.info(
                "Script revision table ready in %s; existing documents unchanged",
                engine.url.database,
            )
        finally:
            engine.dispose()
    else:
        logger.info("DDL exported; --apply creates the script revision table")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
