"""显式升级无限画布存储；只新增字段和表，重复执行前核验真实结构。"""

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
from short_drama.domain import CANVAS_TABLES, Base

logger = logging.getLogger(__name__)
SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "数据库模型"
    / "migrations"
    / "2026-10-05-infinite-canvas"
    / "001-canvas-core.sql"
)
MODE_CHECK = "ck_projects_workspace_mode"
COPY_CHECK = "ck_canvas_upload_mode"
COPY_UPGRADE = "canvas_resource_uploads.copy"
COPY_SQL_PATH = SQL_PATH.with_name("002-resource-copy.sql")
CREATION_SQL_PATH = SQL_PATH.with_name("003-canvas-creation.sql")
DRAWING_SQL_PATH = SQL_PATH.with_name("004-canvas-drawings.sql")
DRAWING_HISTORY_SQL_PATH = SQL_PATH.with_name("005-canvas-drawing-history.sql")
FOLDER_SQL_PATH = SQL_PATH.with_name("006-canvas-project-folders.sql")
TASK_SQL_PATH = SQL_PATH.with_name("007-canvas-task-bindings.sql")
TEXT_SQL_PATH = SQL_PATH.with_name("008-canvas-text-stream.sql")
MODEL_SQL_PATH = SQL_PATH.with_name("009-canvas-model-catalog.sql")
BEEFAPI_SQL_PATH = SQL_PATH.with_name("010-canvas-beefapi-connection.sql")
COPY_UPGRADE_SQL = (
    "ALTER TABLE canvas_resource_uploads DROP CHECK ck_canvas_upload_mode, "
    "ADD CONSTRAINT ck_canvas_upload_mode CHECK (mode IN ('multipart','chunked','copy'))"
)


def statements():
    projects = Base.metadata.tables["projects"]
    check = next(x for x in projects.constraints if x.name == MODE_CHECK)
    original_rule = check._create_rule
    check_ddl = str(AddConstraint(check).compile(dialect=dialect()))
    check._create_rule = original_rule
    result = [
        (
            "column",
            "workspace_mode",
            "ALTER TABLE projects ADD COLUMN "
            + str(CreateColumn(projects.c.workspace_mode).compile(dialect=dialect())),
        ),
        ("check", MODE_CHECK, check_ddl),
    ]
    for table in Base.metadata.sorted_tables:
        if table.name not in CANVAS_TABLES:
            continue
        result.append(
            ("table", table.name, str(CreateTable(table).compile(dialect=dialect())).strip())
        )
        result.extend(
            (
                "index",
                f"{table.name}.{index.name}",
                str(CreateIndex(index).compile(dialect=dialect())),
            )
            for index in sorted(table.indexes, key=lambda x: x.name)
        )
    result.append(("upgrade", COPY_UPGRADE, COPY_UPGRADE_SQL))
    return result


def inspect_schema(connection):
    inspector = inspect(connection)
    present = set(inspector.get_table_names())
    missing, changed = [], []
    if "projects" not in present:
        raise RuntimeError("请先用完整 schema.mysql8.sql 初始化账号与业务表")
    expected_mode = Base.metadata.tables["projects"].c.workspace_mode
    actual_mode = next(
        (c for c in inspector.get_columns("projects") if c["name"] == "workspace_mode"), None
    )
    if actual_mode is None:
        missing.append("column:workspace_mode")
    elif (
        _type_signature(actual_mode["type"]) != _type_signature(expected_mode.type)
        or actual_mode["nullable"]
        or _normalize_sql(actual_mode.get("default")) != _normalize_sql("'standard'")
    ):
        changed.append("projects.workspace_mode")
    for name in ["projects", *sorted(CANVAS_TABLES)]:
        if name not in present:
            missing.append(f"table:{name}")
            continue
        table = Base.metadata.tables[name]
        checks = {
            x["name"]: _normalize_sql(x["sqltext"]) for x in inspector.get_check_constraints(name)
        }
        enforced = set(
            connection.scalars(
                text(
                    "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name "
                    "AND CONSTRAINT_TYPE='CHECK' AND ENFORCED='YES'"
                ),
                {"name": name},
            )
        )
        for check in table.constraints:
            if (
                not isinstance(check, CheckConstraint)
                or name == "projects"
                and check.name != MODE_CHECK
            ):
                continue
            if check.name not in checks:
                (missing if name == "projects" else changed).append(f"check:{check.name}")
            elif (
                name == "canvas_resource_uploads"
                and check.name == COPY_CHECK
                and check.name in enforced
                and checks[check.name] == _normalize_sql("mode IN ('multipart','chunked')")
            ):
                missing.append(f"upgrade:{COPY_UPGRADE}")
            elif checks[check.name] != _normalize_sql(check.sqltext) or check.name not in enforced:
                changed.append(f"{name}.{check.name}:check")
        if name == "projects":
            continue
        columns = {x["name"]: x for x in inspector.get_columns(name)}
        for column in table.columns:
            actual = columns.get(column.name)
            default = str(column.server_default.arg) if column.server_default else None
            if actual is None or (
                _type_signature(actual["type"]) != _type_signature(column.type)
                or actual["nullable"] != column.nullable
                or _normalize_sql(actual.get("default")) != _normalize_sql(default)
            ):
                changed.append(f"{name}.{column.name}:column")
        pk = tuple(inspector.get_pk_constraint(name)["constrained_columns"])
        if pk != tuple(c.name for c in table.primary_key.columns):
            changed.append(f"{name}:primary_key")
        indexes = {
            x["name"]: (tuple(x["column_names"]), bool(x["unique"]))
            for x in inspector.get_indexes(name)
        }
        required = {
            x.name: (tuple(c.name for c in x.columns), bool(x.unique)) for x in table.indexes
        }
        required.update(
            {
                x.name: (tuple(c.name for c in x.columns), True)
                for x in table.constraints
                if isinstance(x, UniqueConstraint)
            }
        )
        for key, signature in required.items():
            if key not in indexes and key in {x.name for x in table.indexes}:
                missing.append(f"index:{name}.{key}")
            elif indexes.get(key) != signature:
                changed.append(f"{name}.{key}:index")
        foreign_keys = {
            x["name"]: (
                tuple(x["constrained_columns"]),
                x["referred_table"],
                tuple(x["referred_columns"]),
                ((x.get("options") or {}).get("ondelete") or "RESTRICT").upper(),
                ((x.get("options") or {}).get("onupdate") or "RESTRICT").upper(),
            )
            for x in inspector.get_foreign_keys(name)
        }
        for foreign in table.foreign_key_constraints:
            signature = (
                tuple(c.name for c in foreign.columns),
                foreign.referred_table.name,
                tuple(e.column.name for e in foreign.elements),
                foreign.ondelete or "RESTRICT",
                foreign.onupdate or "RESTRICT",
            )
            if foreign_keys.get(foreign.name) != signature:
                changed.append(f"{name}.{foreign.name}:foreign_key")
        options = inspector.get_table_options(name)
        if (
            options.get("mysql_engine", "").lower() != "innodb"
            or options.get("mysql_collate") != "utf8mb4_0900_ai_ci"
        ):
            changed.append(f"{name}:storage_options")
    return {
        "database": connection.scalar(text("SELECT DATABASE()")),
        "status": "changed" if changed else "partial" if missing else "ready",
        "missing": sorted(missing),
        "changed": sorted(changed),
    }


def apply(engine):
    executed = []
    with engine.connect() as connection:
        name = "short_drama:canvas:" + str(connection.scalar(text("SELECT DATABASE()")))
        if len(name.encode("utf-8")) > 64:
            name = "short_drama:canvas:" + hashlib.sha256(name.encode()).hexdigest()[:40]
        if connection.scalar(text("SELECT GET_LOCK(:name, 10)"), {"name": name}) != 1:
            raise RuntimeError("另一个画布数据库迁移正在运行")
        try:
            state = inspect_schema(connection)
            if state["changed"]:
                raise RuntimeError("画布结构与 ORM 不符，停止新增：" + json.dumps(state["changed"]))
            missing = set(state["missing"])
            for kind, target, sql in statements():
                if f"{kind}:{target}" in missing or (
                    kind == "index" and f"table:{target.split('.')[0]}" in missing
                ):
                    connection.execute(text(sql))
                    connection.commit()
                    executed.append(f"{kind}:{target}")
            state = inspect_schema(connection)
            if state["status"] != "ready":
                raise RuntimeError("DDL 后结构未通过核验：" + json.dumps(state))
            return {**state, "executed": executed}
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
            connection.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--write-sql", action="store_true")
    args = parser.parse_args()
    configure_logging()
    if args.write_sql:
        SQL_PATH.parent.mkdir(parents=True, exist_ok=True)
        SQL_PATH.write_text(
            "-- 无限画布核心存储；旧库使用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(sql + ";" for kind, _, sql in statements() if kind != "upgrade")
            + "\n",
            encoding="utf-8",
        )
        COPY_SQL_PATH.write_text(
            "-- 已安装前版画布表的旧库增量；优先用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                sql + ";"
                for kind, target, sql in statements()
                if target in {"canvas_resource_copy_sources", COPY_UPGRADE}
                or kind == "index"
                and target.startswith("canvas_resource_copy_sources.")
            )
            + "\n",
            encoding="utf-8",
        )
        logger.info("画布增量 SQL 已更新")
        CREATION_SQL_PATH.write_text(
            "-- 首次完整画布创建的私人准备状态；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                sql + ";"
                for kind, target, sql in statements()
                if target.split(".")[0] in {"canvas_creation_attempts", "canvas_creation_resources"}
            )
            + "\n",
            encoding="utf-8",
        )
        DRAWING_SQL_PATH.write_text(
            "-- 已保存绘图、不可变版本及媒体保护；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                sql + ";"
                for _, target, sql in statements()
                if target.split(".")[0]
                in {"canvas_drawings", "canvas_drawing_versions", "canvas_drawing_media_references"}
            )
            + "\n",
            encoding="utf-8",
        )
        DRAWING_HISTORY_SQL_PATH.write_text(
            "-- 整图历史冻结绘图版本；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                sql + ";"
                for _, target, sql in statements()
                if target.split(".")[0] == "canvas_revision_drawing_references"
            )
            + "\n",
            encoding="utf-8",
        )
        FOLDER_SQL_PATH.write_text(
            "-- 本人项目文件夹与画布归属；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                sql + ";"
                for _, target, sql in statements()
                if target.split(".")[0] in {"canvas_project_folders", "canvas_project_folder_items"}
            )
            + "\n",
            encoding="utf-8",
        )
        TASK_SQL_PATH.write_text(
            "-- 私人画布任务出处、媒体引用和产物；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                sql + ";"
                for _, target, sql in statements()
                if target.split(".")[0]
                in {"canvas_task_bindings", "canvas_task_media_references", "canvas_results"}
            )
            + "\n",
            encoding="utf-8",
        )
        TEXT_SQL_PATH.write_text(
            "-- 私人供应商正文增量与断线游标；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                sql + ";"
                for _, target, sql in statements()
                if target.split(".")[0] == "canvas_task_text_deltas"
            )
            + "\n",
            encoding="utf-8",
        )
        MODEL_SQL_PATH.write_text(
            "-- 本人模型渠道目录及稳定执行配置绑定；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                sql + ";"
                for _, target, sql in statements()
                if target.split(".")[0] in {"canvas_model_catalogs", "canvas_channel_models"}
            )
            + "\n",
            encoding="utf-8",
        )
        BEEFAPI_SQL_PATH.write_text(
            "-- 本人 BeefAPI 设备授权与加密凭据；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                "\n".join(line.rstrip() for line in sql.splitlines()) + ";"
                for _, target, sql in statements()
                if target.split(".")[0] == "canvas_beefapi_connections"
            )
            + "\n",
            encoding="utf-8",
        )
        return
    engine = build_engine(Settings())
    try:
        if args.apply:
            state = apply(engine)
        else:
            with engine.connect() as connection:
                state = inspect_schema(connection)
        logger.info("画布数据库检查：%s", json.dumps(state, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
