"""Metadata base; each table retains its individual audit contract."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "fk": "fk_%(table_name)s_%(column_0_name)s",
            "uq": "uk_%(table_name)s_%(column_0_name)s",
        }
    )
