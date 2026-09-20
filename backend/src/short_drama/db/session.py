from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from short_drama.core.config import Settings


def configure_mysql(engine: Engine) -> Engine:
    @event.listens_for(engine, "connect")
    def initialize_connection(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute("SET NAMES utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            cursor.execute("SET time_zone = '+00:00'")
            cursor.execute("SELECT @@SESSION.sql_mode")
            modes = set(filter(None, cursor.fetchone()[0].split(",")))
            modes.add("STRICT_TRANS_TABLES")
            cursor.execute("SET SESSION sql_mode = %s", (",".join(sorted(modes)),))
        connection.commit()

    return engine


def build_engine(settings: Settings) -> Engine:
    return configure_mysql(
        create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=10,
            hide_parameters=True,
            connect_args={
                "connect_timeout": settings.db_connect_timeout,
                "read_timeout": 10,
                "write_timeout": 10,
            },
        )
    )


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
