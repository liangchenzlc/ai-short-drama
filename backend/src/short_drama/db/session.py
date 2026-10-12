from collections.abc import Iterable
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError


def _guard_legacy_mapper(target: Any) -> None:
    from short_drama.domain import Asset, AssetVersion, AssetVersionMediaReference

    model = inspect(target).mapper.class_
    if issubclass(model, (Asset, AssetVersion, AssetVersionMediaReference)):
        raise WorkflowError(
            "asset_version_bulk_forbidden", "角色及其历史不允许使用 legacy bulk 写入", 403
        )


class AppSession(Session):
    """应用 Session：角色写入须经过实体校验及锁内追加服务。"""

    def bulk_save_objects(
        self,
        objects: Iterable[object],
        return_defaults: bool = False,
        update_changed_only: bool = True,
        preserve_order: bool = True,
    ) -> None:
        values = list(objects)
        for entity in values:
            _guard_legacy_mapper(entity)
        return super().bulk_save_objects(
            values,
            return_defaults=return_defaults,
            update_changed_only=update_changed_only,
            preserve_order=preserve_order,
        )

    def bulk_insert_mappings(
        self,
        mapper: Any,
        mappings: Iterable[dict[str, Any]],
        return_defaults: bool = False,
        render_nulls: bool = False,
    ) -> None:
        _guard_legacy_mapper(mapper)
        return super().bulk_insert_mappings(
            mapper, mappings, return_defaults=return_defaults, render_nulls=render_nulls
        )

    def bulk_update_mappings(self, mapper: Any, mappings: Iterable[dict[str, Any]]) -> None:
        _guard_legacy_mapper(mapper)
        return super().bulk_update_mappings(mapper, mappings)


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
    return sessionmaker(bind=engine, class_=AppSession, expire_on_commit=False, autoflush=False)


# Register access checks once, for both API and worker Session implementations.
from . import access as _access  # noqa: F401, E402
