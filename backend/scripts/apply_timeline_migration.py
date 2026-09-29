"""Upgrade existing assembly drafts without dropping media or edit decisions."""

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text

from short_drama.core.config import Settings
from short_drama.db.session import build_engine


def migrate(connection):
    if "last_edit_receipt" not in {
        c["name"] for c in inspect(connection).get_columns("episode_assemblies")
    }:
        connection.exec_driver_sql(
            "ALTER TABLE episode_assemblies ADD COLUMN last_edit_receipt JSON NULL"
        )
    table = "episode_assembly_clips"
    columns = {c["name"] for c in inspect(connection).get_columns(table)}
    if "client_key" not in columns:
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN client_key CHAR(36) CHARACTER SET ascii NULL"
        )
    if "removed" not in columns:
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN removed INT UNSIGNED NOT NULL DEFAULT 0"
        )
    indexes = {i["name"] for i in inspect(connection).get_indexes(table)}
    if "idx_assembly_clips_shot" not in indexes:
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD INDEX idx_assembly_clips_shot (assembly_id, shot_id)"
        )
    if "uk_assembly_clips_client" not in indexes:
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD UNIQUE KEY uk_assembly_clips_client (assembly_id, client_key)"
        )
    if "uk_assembly_clips_shot" in indexes:
        connection.exec_driver_sql(f"ALTER TABLE {table} DROP INDEX uk_assembly_clips_shot")
    checks = {c["name"] for c in inspect(connection).get_check_constraints(table)}
    if "ck_assembly_clips_removed" not in checks:
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_assembly_clips_removed CHECK (removed IN (0,1))"
        )
    checks = {
        c["name"]: c["sqltext"]
        for c in inspect(connection).get_check_constraints("episode_render_jobs")
    }
    if "preview" not in checks.get("ck_render_jobs_kind", ""):
        if "ck_render_jobs_kind" in checks:
            connection.exec_driver_sql(
                "ALTER TABLE episode_render_jobs DROP CHECK ck_render_jobs_kind"
            )
        connection.exec_driver_sql(
            "ALTER TABLE episode_render_jobs ADD CONSTRAINT ck_render_jobs_kind "
            "CHECK (kind IN ('probe','export','preview'))"
        )


def main():
    engine = build_engine(Settings())
    with engine.connect() as connection:
        active = connection.scalar(
            text("SELECT COUNT(*) FROM episode_render_jobs WHERE status IN ('queued','running')")
        )
        if active:
            raise SystemExit("Wait for active renders before applying the timeline migration.")
        backup = Path(".runtime/schema-backups")
        backup.mkdir(parents=True, exist_ok=True)
        rows = {
            table: [dict(r) for r in connection.execute(text(f"SELECT * FROM {table}")).mappings()]
            for table in ("episode_assemblies", "episode_assembly_clips", "episode_render_jobs")
        }
        path = backup / f"timeline-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")
        connection.commit()
        migrate(connection)
        connection.commit()
    engine.dispose()
    print(f"Timeline migration complete. Pre-migration data backup: {path}")


if __name__ == "__main__":
    main()
