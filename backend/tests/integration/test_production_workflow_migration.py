"""Upgrade a disposable database from the pre-production-workflow schema."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import bindparam, text

pytestmark = pytest.mark.integration

TABLES = ("episodes", "shot_scripts", "assets", "shot_images", "asset_image_candidates")
ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = next((ROOT / "docs").rglob("2026-09-21-production-workflow/000_precheck.sql")).parent


def mysql_statements(path):
    delimiter = ";"
    buffer = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        if line.upper().startswith("DELIMITER "):
            assert not buffer
            delimiter = line.split(maxsplit=1)[1]
            continue
        buffer.append(raw_line)
        if line.endswith(delimiter):
            statement = "\n".join(buffer)
            statement = statement[: statement.rfind(delimiter)].strip()
            if statement:
                yield statement
            buffer = []
    assert not buffer, f"unterminated SQL in {path.name}"


def run_sql(connection, name):
    for statement in mysql_statements(MIGRATIONS / name):
        connection.exec_driver_sql(statement)


def run_backfill(engine):
    path = MIGRATIONS / "004_backfill_asset_candidates.py"
    spec = importlib.util.spec_from_file_location("production_asset_backfill", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = sys.argv
    try:
        sys.argv = [
            str(path),
            "--database-url",
            engine.url.render_as_string(hide_password=False),
            "--apply",
        ]
        assert module.main() == 0
    finally:
        sys.argv = previous


def contract_snapshot(connection):
    table_params = {"names": TABLES}
    columns = connection.execute(
        text(
            "SELECT table_name,column_name,ordinal_position,column_type,is_nullable,column_default,"
            "extra,generation_expression,character_set_name,collation_name,column_comment "
            "FROM information_schema.columns WHERE table_schema=DATABASE() "
            "AND table_name IN :names ORDER BY table_name,ordinal_position"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    indexes = connection.execute(
        text(
            "SELECT table_name,index_name,non_unique,seq_in_index,column_name,collation,index_type "
            "FROM information_schema.statistics WHERE table_schema=DATABASE() "
            "AND table_name IN :names ORDER BY table_name,index_name,seq_in_index"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    constraints = connection.execute(
        text(
            "SELECT table_name,constraint_name,constraint_type,enforced "
            "FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() "
            "AND table_name IN :names ORDER BY table_name,constraint_name"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    keys = connection.execute(
        text(
            "SELECT table_name,constraint_name,column_name,ordinal_position,"
            "referenced_table_name,referenced_column_name "
            "FROM information_schema.key_column_usage WHERE constraint_schema=DATABASE() "
            "AND table_name IN :names ORDER BY table_name,constraint_name,ordinal_position"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    checks = connection.execute(
        text(
            "SELECT tc.table_name,cc.constraint_name,cc.check_clause "
            "FROM information_schema.check_constraints cc "
            "JOIN information_schema.table_constraints tc "
            "ON tc.constraint_schema=cc.constraint_schema "
            "AND tc.constraint_name=cc.constraint_name "
            "WHERE cc.constraint_schema=DATABASE() AND tc.table_name IN :names "
            "ORDER BY tc.table_name,cc.constraint_name"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    return {
        "columns": [tuple(row.values()) for row in columns],
        "indexes": [tuple(row.values()) for row in indexes],
        "constraints": [tuple(row.values()) for row in constraints],
        "keys": [tuple(row.values()) for row in keys],
        "checks": [
            (table_name, constraint_name, re.sub(r"[\s`]", "", clause).lower())
            for table_name, constraint_name, clause in (row.values() for row in checks)
        ],
    }


def rows(connection, table):
    assert table in {
        "episodes",
        "shot_scripts",
        "assets",
        "shot_assets",
        "shot_images",
        "shot_videos",
        "script_shot_records",
        "media_recycle_bin",
    }
    return [
        dict(row)
        for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()
    ]


def test_full_production_migration_is_reentrant_preserves_data_and_matches_canonical(
    migration_mysql_engine,
):
    with migration_mysql_engine.connect() as connection:
        database = connection.scalar(text("SELECT DATABASE()"))
        assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database)
        canonical = contract_snapshot(connection)
        connection.exec_driver_sql(
            "INSERT INTO projects (id,name,aspect) VALUES (9001,'migration','16:9')"
        )
        connection.exec_driver_sql(
            "INSERT INTO episodes (id,project_id,position,title,aspect) "
            "VALUES (9002,9001,1,'episode','16:9')"
        )
        connection.exec_driver_sql(
            "INSERT INTO media_files (id,format_code,storage_locator) "
            "VALUES (9003,'image/png','migration/image.png')"
        )
        connection.exec_driver_sql(
            "INSERT INTO assets (id,kind,name,media_id) VALUES (9004,'prop','umbrella',9003)"
        )
        connection.exec_driver_sql(
            "INSERT INTO episode_assets (id,episode_id,asset_id,position) VALUES (9005,9002,9004,1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO episode_scripts (id,episode_id,position,content,state) "
            "VALUES (9006,9002,1,'source','confirmed')"
        )
        connection.exec_driver_sql(
            "INSERT INTO shot_scripts (id,episode_id,position,script) "
            "VALUES (9007,9002,1,'legacy shot')"
        )
        connection.exec_driver_sql(
            "INSERT INTO shot_assets (id,episode_id,asset_id,shot_id) VALUES (9008,9002,9004,9007)"
        )
        connection.exec_driver_sql(
            "INSERT INTO shot_images (id,episode_id,shot_id,aspect,media_id) "
            "VALUES (9009,9002,9007,'16:9',9003)"
        )
        connection.exec_driver_sql(
            "INSERT INTO script_shot_records (id,script_id,shot_id,batch_id) "
            "VALUES (9010,9006,9007,9011)"
        )
        connection.commit()

        connection.exec_driver_sql("DROP TABLE asset_image_candidates")
        connection.exec_driver_sql(
            "ALTER TABLE shot_images DROP CHECK ck_shot_images_context_hash, "
            "DROP COLUMN context_hash"
        )
        connection.exec_driver_sql(
            "ALTER TABLE shot_scripts "
            "DROP CHECK ck_shot_scripts_duration_ms, "
            "DROP COLUMN source_excerpt, DROP COLUMN duration_ms, "
            "DROP CHECK ck_shot_scripts_row_version, "
            "DROP CHECK ck_shot_scripts_image_settings, "
            "DROP CHECK ck_shot_scripts_deleted_time, "
            "DROP CHECK ck_shot_scripts_creation, "
            "DROP INDEX uk_shots_episode_active_position, "
            "DROP INDEX uk_shots_creation_key, "
            "DROP INDEX idx_shots_episode_deleted_position, "
            "ADD UNIQUE KEY uk_shots_episode_position (episode_id,position), "
            "DROP COLUMN creation_hash, DROP COLUMN creation_key, DROP COLUMN active_position, "
            "DROP COLUMN deleted_at, DROP COLUMN image_settings, DROP COLUMN row_version"
        )
        connection.exec_driver_sql(
            "ALTER TABLE assets "
            "DROP CHECK ck_assets_row_version, DROP CHECK ck_assets_state, "
            "DROP CHECK ck_assets_confirmed_media, DROP CHECK ck_assets_tags, "
            "DROP CHECK ck_assets_scene_time, DROP CHECK ck_assets_creation_pair, "
            "DROP INDEX uk_assets_creation_key, DROP COLUMN creation_hash, "
            "DROP COLUMN creation_key, DROP COLUMN scene_time, DROP COLUMN tags, "
            "DROP COLUMN state, DROP COLUMN row_version"
        )
        connection.exec_driver_sql(
            "ALTER TABLE episodes DROP CHECK ck_episodes_storyboard_version, "
            "DROP COLUMN storyboard_version"
        )
        connection.commit()
        protected_tables = (
            "episodes",
            "shot_scripts",
            "assets",
            "shot_assets",
            "shot_images",
            "shot_videos",
            "script_shot_records",
            "media_recycle_bin",
        )
        before = {table: rows(connection, table) for table in protected_tables}

        for name in (
            "000_precheck.sql",
            "001_episode_storyboard.sql",
            "002_asset_persistence.sql",
            "003_shot_image_context.sql",
        ):
            run_sql(connection, name)
        run_backfill(migration_mysql_engine)
        run_sql(connection, "005_verify.sql")

        for table, expected in before.items():
            migrated = rows(connection, table)
            old_columns = set(expected[0]) if expected else set()
            assert [{key: row[key] for key in old_columns} for row in migrated] == expected
        assert connection.execute(
            text("SELECT asset_id,media_id FROM asset_image_candidates ORDER BY asset_id")
        ).all() == [(9004, 9003)]
        assert connection.scalar(text("SELECT storyboard_version FROM episodes WHERE id=9002")) == 1
        assert connection.scalar(text("SELECT row_version FROM shot_scripts WHERE id=9007")) == 1
        assert connection.scalar(text("SELECT row_version FROM assets WHERE id=9004")) == 1
        assert connection.scalar(text("SELECT context_hash FROM shot_images WHERE id=9009")) is None
        # The canonical schema also includes the later storyboard prompt migration.
        # Apply it after the production migration to reproduce the complete upgrade path.
        prompt_migrations = MIGRATIONS.parent / "2026-09-22-storyboard-prompts"
        for path in sorted(prompt_migrations.glob("*.sql")):
            for statement in mysql_statements(path):
                connection.exec_driver_sql(statement)
        assert contract_snapshot(connection) == canonical

        connection.exec_driver_sql("UPDATE episodes SET storyboard_version=7 WHERE id=9002")
        connection.exec_driver_sql("UPDATE shot_scripts SET row_version=8 WHERE id=9007")
        connection.exec_driver_sql("UPDATE assets SET row_version=9 WHERE id=9004")
        connection.commit()
        for name in (
            "000_precheck.sql",
            "001_episode_storyboard.sql",
            "002_asset_persistence.sql",
            "003_shot_image_context.sql",
        ):
            run_sql(connection, name)
        run_backfill(migration_mysql_engine)
        run_sql(connection, "005_verify.sql")
        assert connection.scalar(text("SELECT storyboard_version FROM episodes WHERE id=9002")) == 7
        assert connection.scalar(text("SELECT row_version FROM shot_scripts WHERE id=9007")) == 8
        assert connection.scalar(text("SELECT row_version FROM assets WHERE id=9004")) == 9
        assert connection.scalar(text("SELECT COUNT(*) FROM asset_image_candidates")) == 1
