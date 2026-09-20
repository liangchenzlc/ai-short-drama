from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import Episode

from .base import BaseDAO


class EpisodeDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, Episode)

    def last_position_for_update(self, project_id: int) -> int:
        """Current read, called only after acquiring the parent project's row lock."""
        return (
            self.session.scalar(
                select(Episode.position)
                .where(Episode.project_id == project_id)
                .order_by(Episode.position.desc())
                .limit(1)
                .with_for_update()
            )
            or 0
        )
