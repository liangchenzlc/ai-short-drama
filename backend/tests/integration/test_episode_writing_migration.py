"""Recreate the old episodes shape only inside the random disposable MySQL fixture."""

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService

pytestmark = pytest.mark.integration


def run_migration(connection):
    migration = next(
        (Path(__file__).resolve().parents[3] / "docs").rglob("001_add_episode_writing.sql")
    )
    source = re.sub(r"(?m)^\s*--.*$", "", migration.read_text(encoding="utf-8"))
    # This script has no delimiters/procedures/semicolons inside quoted literals.
    # Use one connection so PREPARE and @variables survive between statements.
    for statement in source.split(";"):
        if statement.strip():
            connection.exec_driver_sql(statement)


def snapshot(connection, table):
    assert table in {"episodes", "episode_novels", "episode_scripts"}
    return [
        dict(row)
        for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()
    ]


def test_migration_backfills_preserves_rows_and_is_repeatable(mysql_engine, db_session):
    project = ProjectService(db_session).create_project({"name": "Migration", "aspect": "9:16"})
    episodes = [
        EpisodeService(db_session).create_for_project(project.id, {"title": title})
        for title in ("Confirmed first", "Position first", "No scripts")
    ]
    with mysql_engine.connect() as connection:
        database = connection.scalar(text("SELECT DATABASE()"))
        assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database)
        # Removing new columns is test setup only, never part of the shipped migration.
        connection.exec_driver_sql(
            "ALTER TABLE episodes DROP CHECK ck_episodes_content_version, "
            "DROP COLUMN editing_script_id, DROP COLUMN content_version"
        )
        connection.execute(
            text("INSERT INTO episode_novels (id, episode_id, content) VALUES (7100, :id, :body)"),
            {"id": episodes[0].id, "body": "  Legacy novel\r\n正文  "},
        )
        connection.execute(
            text(
                "INSERT INTO episode_scripts (id, episode_id, position, content, state) "
                "VALUES (:id, :episode_id, :position, :content, :state)"
            ),
            [
                {
                    "id": 7101,
                    "episode_id": episodes[0].id,
                    "position": 1,
                    "content": "  Older draft  ",
                    "state": "unconfirmed",
                },
                {
                    "id": 7102,
                    "episode_id": episodes[0].id,
                    "position": 5,
                    "content": "  Confirmed\n",
                    "state": "confirmed",
                },
                {
                    "id": 7103,
                    "episode_id": episodes[1].id,
                    "position": 8,
                    "content": "Smaller id",
                    "state": "unconfirmed",
                },
                {
                    "id": 7104,
                    "episode_id": episodes[1].id,
                    "position": 2,
                    "content": "Smaller position",
                    "state": "unconfirmed",
                },
            ],
        )
        connection.commit()
        old_episodes = snapshot(connection, "episodes")
        old_scripts = snapshot(connection, "episode_scripts")
        old_novels = snapshot(connection, "episode_novels")
        run_migration(connection)
        migrated = snapshot(connection, "episodes")
        by_id = {row["id"]: row for row in migrated}
        assert by_id[episodes[0].id]["editing_script_id"] == 7102
        assert by_id[episodes[1].id]["editing_script_id"] == 7104
        assert by_id[episodes[2].id]["editing_script_id"] is None
        assert all(row["content_version"] == 1 for row in migrated)
        assert [
            {
                key: value
                for key, value in row.items()
                if key not in {"editing_script_id", "content_version"}
            }
            for row in migrated
        ] == old_episodes
        assert snapshot(connection, "episode_scripts") == old_scripts
        assert snapshot(connection, "episode_novels") == old_novels
        run_migration(connection)
        assert snapshot(connection, "episodes") == migrated

        # A later explicit choice and a later intentional NULL must both survive reruns.
        connection.execute(
            text("UPDATE episodes SET editing_script_id=7101, content_version=7 WHERE id=:id"),
            {"id": episodes[0].id},
        )
        connection.execute(
            text("UPDATE episodes SET editing_script_id=NULL, content_version=8 WHERE id=:id"),
            {"id": episodes[1].id},
        )
        connection.commit()
        changed = snapshot(connection, "episodes")
        run_migration(connection)
        assert snapshot(connection, "episodes") == changed
        assert snapshot(connection, "episode_scripts") == old_scripts
        assert snapshot(connection, "episode_novels") == old_novels
        columns = (
            connection.execute(
                text(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='episodes' "
                    "AND COLUMN_NAME IN ('editing_script_id','content_version')"
                )
            )
            .mappings()
            .all()
        )
        definitions = {row["COLUMN_NAME"]: row for row in columns}
        assert definitions["editing_script_id"]["COLUMN_TYPE"] == "bigint unsigned"
        assert definitions["editing_script_id"]["IS_NULLABLE"] == "YES"
        assert definitions["content_version"]["COLUMN_TYPE"] == "bigint unsigned"
        assert definitions["content_version"]["IS_NULLABLE"] == "NO"
        assert definitions["content_version"]["COLUMN_DEFAULT"] == "1"
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='episodes' "
                    "AND COLUMN_NAME='editing_script_id' AND REFERENCED_TABLE_NAME IS NOT NULL"
                )
            )
            == 0
        )
    with pytest.raises(OperationalError) as error, mysql_engine.begin() as connection:
        connection.execute(
            text("UPDATE episodes SET content_version=0 WHERE id=:id"), {"id": episodes[0].id}
        )
    assert error.value.orig.args[0] == 3819
