"""Upgrade only an isolated fixture database from the old six-state contract."""

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from test_generation_execution import seeded

from short_drama.db.session import session_factory
from short_drama.domain import AsyncTask

pytestmark = pytest.mark.integration


def test_old_unknown_tasks_become_failed_and_constraint_rejects_unknown(mysql_engine, db_session):
    factory = session_factory(mysql_engine)
    task_id, _ = seeded(factory)
    with mysql_engine.begin() as connection:
        database = connection.scalar(text("SELECT DATABASE()"))
        assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database)
        connection.exec_driver_sql(
            "ALTER TABLE async_tasks DROP CHECK ck_async_tasks_status, "
            "DROP CHECK ck_async_tasks_terminal_time, "
            "ADD CONSTRAINT ck_async_tasks_status CHECK "
            "(status IN ('queued','running','succeeded','failed','cancelled','unknown')), "
            "ADD CONSTRAINT ck_async_tasks_terminal_time CHECK "
            "((status IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL) OR "
            "(status IN ('queued','running','unknown') AND finished_at IS NULL))"
        )
    with factory.begin() as session:
        task = session.get(AsyncTask, task_id)
        task.status = "unknown"
        task.error = {"code": "message_delivery_unknown"}
    migration = next(
        (Path(__file__).resolve().parents[3] / "docs").rglob("006_remove_task_unknown.sql")
    )
    source = re.sub(r"(?m)^\s*--.*$", "", migration.read_text(encoding="utf-8"))
    with mysql_engine.begin() as connection:
        for statement in source.split(";"):
            statement = statement.strip()
            if not statement or re.match(r"^(USE|SET|START|COMMIT)\b", statement, re.I):
                continue
            assert connection.scalar(text("SELECT DATABASE()")) == database
            assert re.match(r"^(UPDATE async_tasks|ALTER TABLE async_tasks)\b", statement, re.I)
            connection.execute(text(statement))
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        assert task.status == "failed" and task.finished_at is not None
        assert task.message_version == 2 and task.message_status == "idle"
        assert task.next_action == "submit" and task.next_run_at is None
        assert task.error["code"] == "message_delivery_unknown"
    with pytest.raises(OperationalError) as error, mysql_engine.begin() as connection:
        connection.execute(
            text("UPDATE async_tasks SET status='unknown', finished_at=NULL WHERE id=:id"),
            {"id": task_id},
        )
    assert error.value.orig.args[0] == 3819
