"""显式新增图片组、固定图位和私人处理记录；不自动修改现有业务表。"""

import argparse
import logging
import sys

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from short_drama.core.config import Settings
from short_drama.db.readiness import _normalize_sql, _type_signature
from short_drama.db.session import build_engine
from short_drama.domain import ImageGroup, ImageGroupItem, ImageOperation

TABLES = (ImageGroup.__table__, ImageGroupItem.__table__, ImageOperation.__table__)
logger = logging.getLogger(__name__)


def ddl():
    statements = []
    for table in TABLES:
        statements.append(str(CreateTable(table).compile(dialect=dialect())).strip())
        statements.extend(
            str(CreateIndex(index).compile(dialect=dialect()))
            for index in sorted(table.indexes, key=lambda value: value.name)
        )
    return ";\n\n".join(statements) + ";\n"


def apply_ddl(connection):
    statements = []
    for table in TABLES:
        inspector = inspect(connection)
        if not inspector.has_table(table.name):
            statement = str(CreateTable(table).compile(dialect=dialect()))
            connection.exec_driver_sql(statement)
            statements.append(statement)
        verify_table(connection, table)
        indexes = {index["name"] for index in inspect(connection).get_indexes(table.name)}
        for index in sorted(table.indexes, key=lambda value: value.name):
            if index.name not in indexes:
                statement = str(CreateIndex(index).compile(dialect=dialect()))
                connection.exec_driver_sql(statement)
                statements.append(statement)
    return statements


def verify_table(connection, table):
    inspector = inspect(connection)
    actual = {column["name"]: column for column in inspector.get_columns(table.name)}
    if set(actual) != set(table.columns.keys()) or any(
        actual[column.name]["nullable"] != column.nullable
        or _type_signature(actual[column.name]["type"]) != _type_signature(column.type)
        or actual[column.name].get("default") is not None
        for column in table.columns
    ):
        raise RuntimeError(f"{table.name} 列结构与当前模型不一致，请核对已有部署")
    if tuple(inspector.get_pk_constraint(table.name)["constrained_columns"]) != tuple(
        column.name for column in table.primary_key.columns
    ):
        raise RuntimeError(f"{table.name} 主键不匹配")
    indexes = {
        index["name"]: (tuple(index["column_names"]), bool(index["unique"]))
        for index in inspector.get_indexes(table.name)
    }
    required = {
        constraint.name: (tuple(column.name for column in constraint.columns), True)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    required.update(
        {
            index.name: (tuple(column.name for column in index.columns), bool(index.unique))
            for index in table.indexes
            if index.name in indexes
        }
    )
    if any(indexes.get(name) != signature for name, signature in required.items()):
        raise RuntimeError(f"{table.name} 索引定义不匹配，不自动覆盖")
    checks = {
        check["name"]: _normalize_sql(check["sqltext"])
        for check in inspector.get_check_constraints(table.name)
    }
    enforced = set(
        connection.scalars(
            text(
                "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table "
                "AND CONSTRAINT_TYPE='CHECK' AND ENFORCED='YES'"
            ),
            {"table": table.name},
        )
    )
    if any(
        checks.get(check.name) != _normalize_sql(check.sqltext) or check.name not in enforced
        for check in table.constraints
        if isinstance(check, CheckConstraint)
    ):
        raise RuntimeError(f"{table.name} CHECK 定义或启用状态不匹配")
    foreign_keys = {
        key["name"]: (
            tuple(key["constrained_columns"]),
            key["referred_table"],
            tuple(key["referred_columns"]),
            ((key.get("options") or {}).get("ondelete") or "RESTRICT").upper(),
            ((key.get("options") or {}).get("onupdate") or "RESTRICT").upper(),
        )
        for key in inspector.get_foreign_keys(table.name)
    }
    if any(
        foreign_keys.get(key.name)
        != (
            tuple(column.name for column in key.columns),
            key.referred_table.name,
            tuple(element.column.name for element in key.elements),
            (key.ondelete or "RESTRICT").upper(),
            (key.onupdate or "RESTRICT").upper(),
        )
        for key in table.foreign_key_constraints
    ):
        raise RuntimeError(f"{table.name} 外键定义不匹配")
    options = inspector.get_table_options(table.name)
    if any(
        options.get(name, "").casefold() != expected
        for name, expected in (
            ("mysql_engine", "innodb"),
            ("mysql_collate", "utf8mb4_0900_ai_ci"),
            ("mysql_row_format", "dynamic"),
        )
    ):
        raise RuntimeError(f"{table.name} 存储选项不匹配")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.apply:
        sys.stdout.write(ddl())
    else:
        engine = build_engine(Settings())
        try:
            with engine.begin() as connection:
                logger.info(
                    "当前数据库：%s", connection.exec_driver_sql("SELECT DATABASE()").scalar()
                )
                for statement in apply_ddl(connection):
                    logger.info("已执行：%s", statement)
                logger.info("图片组结构已就绪，未改写现有作品和生成记录")
        finally:
            engine.dispose()
