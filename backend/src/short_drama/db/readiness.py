"""Fail closed before serving accounts against a partially migrated database."""

import re

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects.mysql import dialect as mysql_dialect

from short_drama.domain import AGENT_TABLES, Base

OWNERS = {
    "projects": "owner_user_id",
    "ai_model_configs": "owner_user_id",
    "global_assets": "user_id",
}
ROOTS = {"assets", "media_files", "async_tasks", "generation_batches"}
INITIATORS = {"async_tasks", "generation_batches", "episode_render_jobs"}


def schema_gaps(connection, *, include_agent=False):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    required = set(Base.metadata.tables)
    if not include_agent:
        required -= AGENT_TABLES
    gaps = [name for name in required if name not in tables]
    for name in tables & required:
        columns = {c["name"] for c in inspector.get_columns(name)}
        gaps.extend(
            f"{name}.{c.name}" for c in Base.metadata.tables[name].columns if c.name not in columns
        )
    return sorted(gaps)


def _normalize_sql(expression):
    expression = re.sub(
        r"(?<![a-zA-Z0-9'])_(?:utf8mb4|utf8|ascii|binary)(?=')",
        "",
        str(expression),
    )
    tokens = re.findall(
        r"'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"|`(?:``|[^`])*`|"
        r"[a-zA-Z_][a-zA-Z_0-9]*|[0-9]+(?:\.[0-9]+)?|<>|!=|<=|>=|[^\s]",
        expression,
    )
    normalized = []
    for token in tokens:
        if token.startswith("'"):
            normalized.append(token[1:-1] if token[1:-1].isdigit() else token)
        else:
            token = token.strip('`"').casefold()
            normalized.append("length" if token == "octet_length" else token)

    def canonical(parts):
        # MySQL adds redundant parentheses when reflecting expressions. Preserve
        # AND/OR grouping: deleting all parentheses can hide a weakened CHECK.
        while len(parts) > 1 and parts[0] == "(" and parts[-1] == ")":
            depth = 0
            for _outer_index, token in enumerate(parts):
                depth += (token == "(") - (token == ")")
                if depth == 0:
                    break
            if _outer_index != len(parts) - 1:
                break
            parts = parts[1:-1]
        for operator in ("or", "and"):
            depth, start, between = 0, 0, False
            groups = []
            for index, token in enumerate(parts):
                depth += (token == "(") - (token == ")")
                if depth:
                    continue
                if token == "between":
                    between = True
                elif token == "and" and between:
                    between = False
                elif token == operator:
                    groups.append(parts[start:index])
                    start = index + 1
            if groups:
                groups.append(parts[start:])
                return operator + "(" + ",".join(canonical(group) for group in groups) + ")"
        return "".join(token for token in parts if token not in {"(", ")"})

    return canonical(normalized)


def _type_signature(column_type):
    # MySQL reflection makes utf8mb4 explicit on columns with a different
    # utf8mb4 collation, while the declaration inherits the table charset.
    return (
        _normalize_sql(column_type.compile(dialect=mysql_dialect()))
        .replace("'", "")
        .replace("charactersetutf8mb4", "")
    )


def inspect_agent_schema(connection):
    """Read-only structural verification, independent of the feature switch.

    A disabled Agent must not make an otherwise migrated prompt-only database
    unbootable. READY is also needed to read existing shared artifacts after the
    execution feature is disabled. Names alone do not establish readiness.
    """
    inspector = inspect(connection)
    present = set(inspector.get_table_names()) & AGENT_TABLES
    if not present:
        return {"status": "absent", "gaps": sorted(AGENT_TABLES)}
    gaps = [name for name in AGENT_TABLES if name not in present]
    for name in sorted(present):
        expected = Base.metadata.tables[name]
        columns = {column["name"]: column for column in inspector.get_columns(name)}
        for column in expected.columns:
            actual = columns.get(column.name)
            prefix = f"{name}.{column.name}"
            if actual is None:
                gaps.append(prefix)
                continue
            if _type_signature(actual["type"]) != _type_signature(column.type):
                gaps.append(f"{prefix}:type")
            if actual["nullable"] != column.nullable:
                gaps.append(f"{prefix}:nullable")
            if column.computed is not None:
                computed = actual.get("computed") or {}
                if (
                    _normalize_sql(computed.get("sqltext", ""))
                    != _normalize_sql(column.computed.sqltext)
                    or computed.get("persisted") is not True
                ):
                    gaps.append(f"{prefix}:computed")
            else:
                default = str(column.server_default.arg) if column.server_default else None
                actual_default = actual.get("default")
                if (default is None) != (actual_default is None) or (
                    default is not None
                    and _normalize_sql(default) != _normalize_sql(actual_default)
                ):
                    gaps.append(f"{prefix}:default")
        if tuple(inspector.get_pk_constraint(name)["constrained_columns"]) != tuple(
            column.name for column in expected.primary_key.columns
        ):
            gaps.append(f"{name}:primary_key")
        indexes = {
            index["name"]: (tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(name)
        }
        required_indexes = {
            index.name: (tuple(column.name for column in index.columns), bool(index.unique))
            for index in expected.indexes
        }
        required_indexes.update(
            {
                constraint.name: (tuple(column.name for column in constraint.columns), True)
                for constraint in expected.constraints
                if isinstance(constraint, UniqueConstraint)
            }
        )
        for key, signature in required_indexes.items():
            if indexes.get(key) != signature:
                gaps.append(f"{name}.{key}:index")
        foreign_keys = {
            fk["name"]: (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                ((fk.get("options") or {}).get("ondelete") or "RESTRICT").upper(),
                ((fk.get("options") or {}).get("onupdate") or "RESTRICT").upper(),
            )
            for fk in inspector.get_foreign_keys(name)
        }
        for fk in expected.foreign_key_constraints:
            signature = (
                tuple(column.name for column in fk.columns),
                fk.referred_table.name,
                tuple(element.column.name for element in fk.elements),
                "RESTRICT",
                "RESTRICT",
            )
            if foreign_keys.get(fk.name) != signature:
                gaps.append(f"{name}.{fk.name}:foreign_key")
        checks = {
            check["name"]: _normalize_sql(check["sqltext"])
            for check in inspector.get_check_constraints(name)
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
        for check in expected.constraints:
            if isinstance(check, CheckConstraint) and (
                check.name not in enforced
                or checks.get(check.name) != _normalize_sql(check.sqltext)
            ):
                gaps.append(f"{name}.{check.name}:check")
        options = inspector.get_table_options(name)
        if options.get("mysql_engine", "").casefold() != "innodb":
            gaps.append(f"{name}:engine")
        if options.get("mysql_collate", "").casefold() != "utf8mb4_0900_ai_ci":
            gaps.append(f"{name}:collation")
        if options.get("mysql_row_format", "").casefold() != "dynamic":
            gaps.append(f"{name}:row_format")
    return {"status": "partial" if gaps else "ready", "gaps": sorted(set(gaps))}


def assert_agent_ready(engine, settings):
    if not getattr(settings, "agent_enabled", False):
        return
    if not settings.auth_enabled:
        raise RuntimeError("Agent mode requires authenticated accounts.")
    with engine.connect() as connection:
        state = inspect_agent_schema(connection)
    if state["status"] != "ready":
        raise RuntimeError(
            "Agent schema incomplete or changed. Run scripts/agent_migration.py --precheck "
            "and --apply before enabling Agent mode."
        )


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
