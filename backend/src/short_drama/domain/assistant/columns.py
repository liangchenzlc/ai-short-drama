from sqlalchemy import (
    ForeignKeyConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, CHAR, DATETIME, JSON
from sqlalchemy.orm import mapped_column


def _id():
    return mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=False)


def _number(default=None, *, nullable=False):
    return mapped_column(
        BIGINT(unsigned=True),
        nullable=nullable,
        server_default=text(str(default)) if default is not None else None,
    )


def _time(*, nullable=False):
    return mapped_column(
        DATETIME(fsp=6),
        nullable=nullable,
        server_default=None if nullable else text("CURRENT_TIMESTAMP(6)"),
    )


def _json(*, nullable=False, array=False):
    return mapped_column(
        JSON(none_as_null=nullable),
        nullable=nullable,
        server_default=None if nullable else text("(JSON_ARRAY())" if array else "(JSON_OBJECT())"),
    )


def _hash(*, nullable=False):
    return mapped_column(CHAR(64, charset="ascii", collation="ascii_bin"), nullable=nullable)


def _fk(fields, table, targets=None, *, name):
    return ForeignKeyConstraint(
        fields,
        [f"{table}.{field}" for field in (targets or ["id"])],
        name=name,
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )
