from sqlalchemy.orm import Session

from short_drama.domain import Episode

from .base import BaseDAO


class EpisodeDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, Episode)
