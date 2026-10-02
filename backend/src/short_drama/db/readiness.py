"""Fail closed before serving accounts against a partially migrated database."""

import re

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text

from short_drama.domain import Base

OWNERS = {
    "projects": "owner_user_id",
    "ai_model_configs": "owner_user_id",
    "global_assets": "user_id",
}
ROOTS = {"assets", "media_files", "async_tasks", "generation_batches"}
INITIATORS = {"async_tasks", "generation_batches", "episode_render_jobs"}


def schema_gaps(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    gaps = [name for name in Base.metadata.tables if name not in tables]
    for name in tables & set(Base.metadata.tables):
        columns = {c["name"] for c in inspector.get_columns(name)}
        gaps.extend(
            f"{name}.{c.name}" for c in Base.metadata.tables[name].columns if c.name not in columns
        )
    return sorted(gaps)


def ownership_gaps(connection):
    checks = {name: f"`{field}` IS NULL" for name, field in OWNERS.items()}
    for name in ROOTS:
        checks[name] = "(scope_user_id IS NULL) = (project_id IS NULL)"
    for name in INITIATORS:
        checks[name] = (
            f"({checks[name]}) OR initiated_by IS NULL"
            if name in checks
            else "initiated_by IS NULL"
        )
    return {
        name: count
        for name, predicate in checks.items()
        if (count := connection.scalar(text(f"SELECT COUNT(*) FROM `{name}` WHERE {predicate}")))
    }


def assert_identity_ready(engine, settings):
    if not settings.auth_enabled:
        return
    with engine.connect() as connection:
        gaps = schema_gaps(connection)
        if gaps:
            raise RuntimeError(
                "Collaboration schema incomplete. Run collaboration_migration.py --expand, "
                "explicit --backfill and --finalize before starting services."
            )
        if ownership_gaps(connection):
            raise RuntimeError(
                "Historical ownership is unresolved. Explicitly assign the legacy owner "
                "with collaboration_migration.py; registration never assigns historical data."
            )
        inspector = inspect(connection)
        for name, field in OWNERS.items():
            if next(c for c in inspector.get_columns(name) if c["name"] == field)["nullable"]:
                raise RuntimeError(
                    "Collaboration migration is not finalized. "
                    "Run --finalize before starting services."
                )
        for name in ROOTS:
            checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints(name)}
            required = {
                c.name: str(c.sqltext)
                for c in Base.metadata.tables[name].constraints
                if isinstance(c, CheckConstraint) and "scope" in (c.name or "")
            }
            enforced = set(
                connection.scalars(
                    text(
                        "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table "
                        "AND CONSTRAINT_TYPE='CHECK' AND ENFORCED='YES'"
                    ),
                    {"table": name},
                )
            )

            def normalize(expression):
                return re.sub(r"[\s`()]+", "", expression).casefold()

            if any(
                key not in enforced or normalize(checks.get(key, "")) != normalize(expression)
                for key, expression in required.items()
            ):
                raise RuntimeError(
                    "Collaboration scope constraints missing, changed or disabled. "
                    "Run --finalize before starting services."
                )
        for name in ROOTS | set(OWNERS) | INITIATORS:
            present = {
                f["name"]: (
                    tuple(f["constrained_columns"]),
                    f["referred_table"],
                    tuple(f["referred_columns"]),
                )
                for f in inspector.get_foreign_keys(name)
            }
            expected = {
                fk.name: (
                    tuple(column.name for column in fk.columns),
                    fk.referred_table.name,
                    tuple(element.column.name for element in fk.elements),
                )
                for fk in Base.metadata.tables[name].foreign_key_constraints
            }
            if any(present.get(key) != signature for key, signature in expected.items()):
                raise RuntimeError("Collaboration foreign keys missing. Run --finalize.")
        for name in {
            "users",
            "project_members",
            "user_model_preferences",
            "user_project_states",
            "ai_model_configs",
            "global_assets",
        }:
            expected = {
                tuple(column.name for column in constraint.columns)
                for constraint in Base.metadata.tables[name].constraints
                if isinstance(constraint, UniqueConstraint)
            }
            present = {
                tuple(index["column_names"])
                for index in inspector.get_indexes(name)
                if index["unique"]
            }
            if not expected <= present:
                raise RuntimeError("Collaboration uniqueness constraints missing. Run --finalize.")
