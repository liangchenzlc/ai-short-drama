"""Real MySQL only; each run owns a new, disposable database."""

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from short_drama.db.session import configure_mysql


@pytest.fixture(scope="session")
def mysql_engine():
    configured = os.environ.get("TEST_DATABASE_URL")
    if not configured:
        pytest.skip("TEST_DATABASE_URL is absent; real MySQL integration tests skipped")
    url = make_url(configured)
    if url.drivername != "mysql+pymysql" or not (url.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must use mysql+pymysql and a database ending in _test")
    database = f"short_drama_{uuid4().hex}_test"
    assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database)
    server = create_engine(
        url.set(database=""),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    created = False
    engine = None
    try:
        try:
            with server.connect() as connection:
                connection.exec_driver_sql(
                    f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
                created = True
        except Exception:
            pytest.fail(
                "MySQL is unavailable or cannot create an isolated test database", pytrace=False
            )
        engine = configure_mysql(
            create_engine(
                url.set(database=database),
                pool_pre_ping=True,
                hide_parameters=True,
            )
        )
        sql_path = next((Path(__file__).resolve().parents[3] / "docs").rglob("schema.mysql8.sql"))
        # This canonical DDL has no semicolons in quoted values or multiline comments.
        source = re.sub(r"(?m)^\s*--.*$", "", sql_path.read_text(encoding="utf-8"))
        with engine.begin() as connection:
            for statement in source.split(";"):
                if statement.strip():
                    connection.execute(text(statement))
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with server.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE `{database}`")
        server.dispose()


@pytest.fixture
def db_session(mysql_engine):
    from short_drama.domain.base import Base

    with Session(mysql_engine, expire_on_commit=False, autoflush=False) as session:
        yield session
    with mysql_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture
def mysql_version(mysql_engine):
    with mysql_engine.connect() as connection:
        return connection.scalar(text("SELECT VERSION()"))
