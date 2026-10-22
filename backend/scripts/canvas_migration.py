"""显式升级无限画布存储；执行前核验真实结构，支持精确旧版升级与重入。"""

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
TOOL_RUN_SQL_PATH = SQL_PATH.with_name("011-canvas-tool-runs.sql")
STORYBOARD_SQL_PATH = SQL_PATH.with_name("012-canvas-storyboard-run.sql")
RENDER_SQL_PATH = SQL_PATH.with_name("013-canvas-render-jobs.sql")
LOCAL_SQL_PATH = SQL_PATH.with_name("014-canvas-local-jobs.sql")
GENERATION_DELETE_SQL_PATH = SQL_PATH.with_name("015-canvas-generation-media-deletions.sql")
LOCAL_DISPOSAL_SQL_PATH = SQL_PATH.with_name("016-canvas-local-disposal.sql")
CREATION_DELETE_SQL_PATH = SQL_PATH.with_name("017-canvas-creation-media-deletions.sql")
CREATION_DELETE_UPGRADE = "canvas_resource_deletions.creation"
CREATION_DELETE_FIELDS = {"upload_id", "idempotency_hash", "request_hash"}
CREATION_DELETE_ADDITIONS = {"creation_resource_id", "disposal_receipt_id"}
DISPOSAL_TABLES = {"canvas_local_jobs": "local", "canvas_render_jobs": "render"}
LEGACY_LOCAL_OUTPUT = (
    "status <> 'succeeded' OR (result_json IS NOT NULL AND finished_at IS NOT NULL "
    "AND (task_type <> 'depth_capture' OR output_media_id IS NOT NULL))"
)
LEGACY_RENDER_OUTPUT = (
    "status <> 'succeeded' OR (output_media_id IS NOT NULL AND result_json IS NOT NULL "
    "AND finished_at IS NOT NULL)"
)
STORYBOARD_UPGRADE = "canvas_tool_runs.storyboard_source"
STORYBOARD_UPGRADE_SQL = (
    "ALTER TABLE canvas_tool_runs MODIFY COLUMN source_media_id BIGINT UNSIGNED NULL"
)
COPY_UPGRADE_SQL = (
    "ALTER TABLE canvas_resource_uploads DROP CHECK ck_canvas_upload_mode, "
    "ADD CONSTRAINT ck_canvas_upload_mode CHECK (mode IN ('multipart','chunked','copy'))"
)


def disposal_upgrade_sql(name: str) -> str:
    table = Base.metadata.tables[name]
    prefix = DISPOSAL_TABLES[name]
    clauses = [
        "ADD COLUMN " + str(CreateColumn(table.c.disposal_receipt_id).compile(dialect=dialect()))
    ]
    if prefix == "local":
        clauses.append("DROP FOREIGN KEY fk_canvas_local_jobs_source_media_id")
        clauses.append(
            "MODIFY COLUMN " + str(CreateColumn(table.c.source_media_id).compile(dialect=dialect()))
        )
    clauses.append(f"DROP CHECK ck_canvas_{prefix}_output")
    names = {
        f"ck_canvas_{prefix}_output",
        f"ck_canvas_{prefix}_disposal",
        f"fk_canvas_{prefix}_disposal",
    }
    if prefix == "local":
        names.add("ck_canvas_local_source")
        names.add("fk_canvas_local_source")
    for constraint in sorted(table.constraints, key=lambda item: item.name or ""):
        if constraint.name in names:
            original_rule = constraint._create_rule
            sql = str(AddConstraint(constraint).compile(dialect=dialect()))
            constraint._create_rule = original_rule
            clauses.append("ADD " + sql.split(" ADD ", 1)[1])
    clauses.append(f"ADD INDEX idx_canvas_{prefix}_disposal (disposal_receipt_id)")
    return f"ALTER TABLE {name} " + ", ".join(clauses)


def legacy_disposal(name: str, columns: dict, checks: dict, enforced: set) -> bool:
    prefix = DISPOSAL_TABLES.get(name)
    if prefix is None or "disposal_receipt_id" in columns:
        return False
    if f"ck_canvas_{prefix}_disposal" in checks:
        return False
    legacy = LEGACY_LOCAL_OUTPUT if prefix == "local" else LEGACY_RENDER_OUTPUT
    if (
        checks.get(f"ck_canvas_{prefix}_output") != _normalize_sql(legacy)
        or f"ck_canvas_{prefix}_output" not in enforced
    ):
        return False
    return prefix == "render" or (
        "ck_canvas_local_source" not in checks
        and "source_media_id" in columns
        and not columns["source_media_id"]["nullable"]
    )


def creation_deletion_upgrade_sql() -> str:
    table = Base.metadata.tables["canvas_resource_deletions"]
    clauses = [
        "MODIFY COLUMN " + str(CreateColumn(table.c[name]).compile(dialect=dialect()))
        for name in sorted(CREATION_DELETE_FIELDS)
    ]
    clauses.extend(
        "ADD COLUMN " + str(CreateColumn(table.c[name]).compile(dialect=dialect()))
        for name in sorted(CREATION_DELETE_ADDITIONS)
    )
    names = {
        "ck_canvas_deletion_origin",
        "fk_canvas_deletion_creation_resource",
        "fk_canvas_deletion_disposal",
        "uk_canvas_deletion_creation_resource",
    }
    for constraint in sorted(table.constraints, key=lambda item: item.name or ""):
        if constraint.name in names:
            original_rule = constraint._create_rule
            sql = str(AddConstraint(constraint).compile(dialect=dialect()))
            constraint._create_rule = original_rule
            clauses.append("ADD " + sql.split(" ADD ", 1)[1])
    clauses.append("ADD INDEX idx_canvas_deletion_disposal (disposal_receipt_id)")
    return "ALTER TABLE canvas_resource_deletions " + ", ".join(clauses)


def legacy_creation_deletion(name: str, columns: dict, checks: dict) -> bool:
    if (
        name != "canvas_resource_deletions"
        or CREATION_DELETE_ADDITIONS.intersection(columns)
        or "ck_canvas_deletion_origin" in checks
    ):
        return False
    table = Base.metadata.tables[name]
    return all(
        field in columns
        and not columns[field]["nullable"]
        and _type_signature(columns[field]["type"]) == _type_signature(table.c[field].type)
        and _normalize_sql(columns[field].get("default")) == _normalize_sql(None)
        for field in CREATION_DELETE_FIELDS
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
    result.append(("upgrade", STORYBOARD_UPGRADE, STORYBOARD_UPGRADE_SQL))
    result.extend(
        ("upgrade", name + ".disposal", disposal_upgrade_sql(name)) for name in DISPOSAL_TABLES
    )
    result.append(("upgrade", CREATION_DELETE_UPGRADE, creation_deletion_upgrade_sql()))
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
        columns = {x["name"]: x for x in inspector.get_columns(name)}
        old_disposal = legacy_disposal(name, columns, checks, enforced)
        old_creation_deletion = legacy_creation_deletion(name, columns, checks)
        prefix = DISPOSAL_TABLES.get(name)
        if old_disposal:
            missing.append("upgrade:" + name + ".disposal")
        if old_creation_deletion:
            missing.append("upgrade:" + CREATION_DELETE_UPGRADE)
        for check in table.constraints:
            if (
                not isinstance(check, CheckConstraint)
                or name == "projects"
                and check.name != MODE_CHECK
            ):
                continue
            if old_disposal and check.name in {
                f"ck_canvas_{prefix}_output",
                f"ck_canvas_{prefix}_disposal",
                "ck_canvas_local_source",
            }:
                continue
            if old_creation_deletion and check.name == "ck_canvas_deletion_origin":
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
        for column in table.columns:
            actual = columns.get(column.name)
            default = str(column.server_default.arg) if column.server_default else None
            if old_creation_deletion and (
                column.name in CREATION_DELETE_ADDITIONS
                and actual is None
                or column.name in CREATION_DELETE_FIELDS
                and actual is not None
            ):
                continue
            if old_disposal and (
                column.name == "disposal_receipt_id"
                and actual is None
                or prefix == "local"
                and column.name == "source_media_id"
                and actual is not None
                and not actual["nullable"]
                and _type_signature(actual["type"]) == _type_signature(column.type)
                and _normalize_sql(actual.get("default")) == _normalize_sql(default)
            ):
                continue
            if (
                name == "canvas_tool_runs"
                and column.name == "source_media_id"
                and actual is not None
                and not actual["nullable"]
                and column.nullable
                and _type_signature(actual["type"]) == _type_signature(column.type)
                and _normalize_sql(actual.get("default")) == _normalize_sql(default)
            ):
                missing.append("upgrade:" + STORYBOARD_UPGRADE)
                continue
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
            if (
                old_creation_deletion
                and key
                in {
                    "uk_canvas_deletion_creation_resource",
                    "idx_canvas_deletion_disposal",
                }
                and key not in indexes
            ):
                continue
            if old_disposal and key == f"idx_canvas_{prefix}_disposal" and key not in indexes:
                continue
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
            if (
                old_creation_deletion
                and foreign.name
                in {
                    "fk_canvas_deletion_creation_resource",
                    "fk_canvas_deletion_disposal",
                }
                and foreign.name not in foreign_keys
            ):
                continue
            if (
                old_disposal
                and foreign.name == f"fk_canvas_{prefix}_disposal"
                and foreign.name not in foreign_keys
            ):
                continue
            signature = (
                tuple(c.name for c in foreign.columns),
                foreign.referred_table.name,
                tuple(e.column.name for e in foreign.elements),
                foreign.ondelete or "RESTRICT",
                foreign.onupdate or "RESTRICT",
            )
            actual_name = (
                "fk_canvas_local_jobs_source_media_id"
                if old_disposal and foreign.name == "fk_canvas_local_source"
                else foreign.name
            )
            if foreign_keys.get(actual_name) != signature:
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
            + "\n\n".join(
                (
                    "\n".join(line.rstrip() for line in sql.splitlines())
                    if target.split(".")[0]
                    in {"canvas_generation_media_deletions", "canvas_resource_deletions"}
                    else sql
                )
                + ";"
                for kind, target, sql in statements()
                if kind != "upgrade"
            )
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
        TOOL_RUN_SQL_PATH.write_text(
            "-- 本人审美执行租约与阶段批准；用 canvas_migration.py --apply 安全重入。\n"
            + "\n\n".join(
                "\n".join(line.rstrip() for line in sql.splitlines()) + ";"
                for _, target, sql in statements()
                if target.split(".")[0] in {"canvas_tool_runs", "canvas_tool_submissions"}
            )
            + "\n",
            encoding="utf-8",
        )
        STORYBOARD_SQL_PATH.write_text(
            "-- 已有011库允许无图片的私人分镜执行；用canvas_migration.py --apply核验并安全重入。\n"
            + STORYBOARD_UPGRADE_SQL
            + ";\n",
            encoding="utf-8",
        )
        RENDER_SQL_PATH.write_text(
            "-- 私人画布本地渲染与冻结媒体引用；用canvas_migration.py --apply核验并安全重入。\n"
            + "\n\n".join(
                "\n".join(line.rstrip() for line in sql.splitlines()) + ";"
                for kind, target, sql in statements()
                if kind != "upgrade"
                and target.split(".")[0] in {"canvas_render_jobs", "canvas_render_media_references"}
            )
            + "\n",
            encoding="utf-8",
        )
        LOCAL_SQL_PATH.write_text(
            "-- 本人媒体转写、深度捕捉与兼容播放；用canvas_migration.py --apply核验并安全重入。\n"
            + "\n\n".join(
                "\n".join(line.rstrip() for line in sql.splitlines()) + ";"
                for kind, target, sql in statements()
                if kind != "upgrade" and target.split(".")[0] == "canvas_local_jobs"
            )
            + "\n",
            encoding="utf-8",
        )
        GENERATION_DELETE_SQL_PATH.write_text(
            "-- 本人已处置画布的生成媒体退役证据；用canvas_migration.py --apply核验并安全重入。\n"
            + "\n\n".join(
                "\n".join(line.rstrip() for line in sql.splitlines()) + ";"
                for _, target, sql in statements()
                if target.split(".")[0] == "canvas_generation_media_deletions"
            )
            + "\n",
            encoding="utf-8",
        )
        LOCAL_DISPOSAL_SQL_PATH.write_text(
            "-- 本人已处置本地/渲染任务释放媒体外键；"
            "用canvas_migration.py --apply精确核验旧结构。\n"
            + "\n\n".join(disposal_upgrade_sql(name) + ";" for name in DISPOSAL_TABLES)
            + "\n",
            encoding="utf-8",
        )
        CREATION_DELETE_SQL_PATH.write_text(
            "-- 早期创建媒体以原准备记录与本人处置回执回收；"
            "用canvas_migration.py --apply精确核验旧结构。\n"
            + creation_deletion_upgrade_sql()
            + ";\n",
            encoding="utf-8",
        )
        return
    engine = build_engine(Settings())
    try:
        with engine.connect() as connection:
            legacy = {"episodes", "project_members", "project_assets"} & set(
                inspect(connection).get_table_names()
            )
            if legacy:
                raise RuntimeError(
                    "旧分集／协作版本不可叠加画布增量，"
                    "请按 docs/instruction/创作模式维护说明.md 离线备份、预演并切换。"
                )
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
