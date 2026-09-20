"""Metadata base; each table retains its individual audit contract."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
