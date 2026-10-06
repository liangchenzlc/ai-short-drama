from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.domain import CanvasCreationAttempt, CanvasCreationResource


class CanvasCreationDAO:
    def __init__(self, session: Session):
        self.session = session

    def by_key(self, user_id: int, key: str, *, lock: bool = True):
        query = select(CanvasCreationAttempt).where(
            CanvasCreationAttempt.user_id == user_id,
            CanvasCreationAttempt.idempotency_key == key,
        )
        return self.session.scalar(self._lock(query) if lock else query)

    def attempt(self, identifier: int, *, lock: bool = True):
        query = select(CanvasCreationAttempt).where(CanvasCreationAttempt.id == identifier)
        return self.session.scalar(self._lock(query) if lock else query)

    def resources(self, attempt_id: int, *, lock: bool = True):
        query = (
            select(CanvasCreationResource)
            .where(CanvasCreationResource.attempt_id == attempt_id)
            .order_by(CanvasCreationResource.source_resource_id)
        )
        return list(self.session.scalars(self._lock(query) if lock else query))

    def pending_count(self, user_id: int, now: datetime) -> int:
        return (
            self.session.scalar(
                select(func.count(CanvasCreationAttempt.id)).where(
                    CanvasCreationAttempt.user_id == user_id,
                    CanvasCreationAttempt.status == "pending",
                    CanvasCreationAttempt.expires_at > now,
                )
            )
            or 0
        )

    def expired(self, now: datetime, limit: int):
        return list(
            self.session.scalars(
                select(CanvasCreationAttempt.id)
                .where(
                    CanvasCreationAttempt.status == "pending",
                    CanvasCreationAttempt.expires_at <= now,
                )
                .order_by(CanvasCreationAttempt.updated_at, CanvasCreationAttempt.id)
                .limit(limit)
            )
        )

    @staticmethod
    def _lock(query):
        return query.with_for_update().execution_options(populate_existing=True)
