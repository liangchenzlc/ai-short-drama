"""离线备份并迁移配置模块；旧局部记忆不提升为账号记忆。"""

import argparse
import json
import logging
import re
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from short_drama.core.config import Settings
from short_drama.db.session import configure_mysql

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
SQL_FILE = ROOT / "docs/database/migrations/2026-11-03-settings-module/001-settings.sql"
BACKUP_TABLES = (
    "agent_preferences",
    "agent_tasks",
    "agent_messages",
    "agent_memory_items",
    "agent_memory_sources",
    "agent_memory_revisions",
    "agent_memory_forget_rules",
    "agent_memory_jobs",
    "mcp_access_tokens",
    "ai_model_configs",
)
RETIRED_KEYS = {
    "scope_type",
    "scope_key",
    "project_id",
    "conversation_id",
    "message_id",
    "source_seq",
    "subject_keys",
    "source_message_ids",
    "sources",
}


def clean_snapshot(value):
    if isinstance(value, dict):
        return {key: clean_snapshot(item) for key, item in value.items() if key not in RETIRED_KEYS}
    if isinstance(value, list):
        return [clean_snapshot(item) for item in value]
    return value


def inspect_impact(connection):
    return {
        "local_memory": connection.scalar(
            text("SELECT COUNT(*) FROM agent_memory_items WHERE scope_type<>'user'")
        ),
        "active_tokens": connection.scalar(
            text("SELECT COUNT(*) FROM mcp_access_tokens WHERE deleted_at IS NULL")
        ),
        "runninghub_images": connection.scalar(
            text(
                "SELECT COUNT(*) FROM ai_model_configs WHERE is_deleted=0 AND service_type='image' "
                "AND (LOWER(provider)='runninghub' OR JSON_UNQUOTE("
                "JSON_EXTRACT(runtime_profile,'$.protocol'))='runninghub-workflow-image')"
            )
        ),
    }


def export_backup(connection, directory):
    directory.mkdir(parents=True, exist_ok=False)
    for table in BACKUP_TABLES:
        rows = [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]
        (directory / f"{table}.json").write_text(
            json.dumps(rows, ensure_ascii=False, default=str, indent=2), encoding="utf-8"
        )
        ddl = connection.execute(text(f"SHOW CREATE TABLE {table}")).one()[1]
        (directory / f"{table}.sql").write_text(ddl + ";\n", encoding="utf-8")


def prepare_data(connection):
    for child in ("agent_memory_sources", "agent_memory_revisions", "agent_memory_forget_rules"):
        connection.execute(
            text(
                f"DELETE child FROM {child} child JOIN agent_memory_items item "
                "ON item.id=child.memory_id WHERE item.scope_type<>'user'"
            )
        )
    connection.execute(text("DELETE FROM agent_memory_items WHERE scope_type<>'user'"))
    connection.execute(
        text(
            "DELETE duplicate FROM agent_memory_sources duplicate "
            "JOIN agent_memory_sources original "
            "ON duplicate.memory_id=original.memory_id "
            "AND duplicate.content_version=original.content_version "
            "AND duplicate.source_hash=original.source_hash AND duplicate.id>original.id"
        )
    )
    for row in connection.execute(
        text("SELECT id,snapshot FROM agent_memory_revisions")
    ).mappings():
        snapshot = (
            json.loads(row["snapshot"]) if isinstance(row["snapshot"], str) else row["snapshot"]
        )
        connection.execute(
            text("UPDATE agent_memory_revisions SET snapshot=:snapshot WHERE id=:id"),
            {"snapshot": json.dumps(clean_snapshot(snapshot), ensure_ascii=False), "id": row["id"]},
        )
    connection.execute(
        text(
            "UPDATE agent_memory_jobs SET status='cancelled',message_status='idle',"
            "finished_at=UTC_TIMESTAMP(6),lease_token=NULL,lease_until=NULL,"
            "publish_token=NULL,publish_until=NULL WHERE kind='extract' "
            "AND status IN ('pending','running')"
        )
    )
    connection.execute(
        text("UPDATE mcp_access_tokens SET deleted_at=UTC_TIMESTAMP(6) WHERE deleted_at IS NULL")
    )
    connection.execute(
        text(
            "UPDATE ai_model_configs SET is_default=0,enabled=0,is_deleted=1,"
            "row_version=row_version+1,updated_at=UTC_TIMESTAMP(6) "
            "WHERE is_deleted=0 AND service_type='image' AND (LOWER(provider)='runninghub' "
            "OR JSON_UNQUOTE(JSON_EXTRACT(runtime_profile,'$.protocol'))="
            "'runninghub-workflow-image')"
        )
    )


def migrate(engine, backup_directory: Path, *, apply: bool):
    if "scope_type" not in {
        column["name"] for column in inspect(engine).get_columns("agent_memory_items")
    }:
        logger.info("配置模块已是账号记忆结构，无需重复迁移")
        return
    with engine.connect() as connection:
        logger.info("迁移影响：%s", json.dumps(inspect_impact(connection)))
        if not apply:
            return
        live = connection.scalar(
            text("SELECT COUNT(*) FROM agent_runs WHERE lease_until>UTC_TIMESTAMP(6)")
        )
        if live:
            raise RuntimeError("存在有效 Agent 租约，请先停止入口并等待持久边界")
        media = connection.scalar(
            text(
                "SELECT COUNT(*) FROM async_tasks task JOIN ai_generation_records record "
                "ON record.task_id=task.id WHERE task.service_type='image' "
                "AND task.status IN ('queued','running') AND record.adapter LIKE '%runninghub%'"
            )
        )
        if media:
            raise RuntimeError("存在未结算图片任务，请先完成结算再退役图片协议")
        export_backup(connection, backup_directory)
        connection.commit()
    with engine.begin() as connection:
        prepare_data(connection)
    sql = re.sub(r"(?m)^\s*--.*$", "", SQL_FILE.read_text(encoding="utf-8"))
    with engine.begin() as connection:
        for statement in sql.split(";"):
            if statement.strip():
                connection.execute(text(statement))
    logger.info("配置模块迁移完成；备份：%s", backup_directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    engine = configure_mysql(create_engine(Settings().database_url, hide_parameters=True))
    try:
        migrate(engine, args.backup_dir.resolve(), apply=args.apply)
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
