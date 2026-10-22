"""Shared clock matching MySQL DATETIME's naive UTC representation."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
