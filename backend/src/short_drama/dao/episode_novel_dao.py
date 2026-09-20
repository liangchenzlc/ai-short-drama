from sqlalchemy.orm import Session

from short_drama.domain import EpisodeNovel

from .base import BaseDAO


class EpisodeNovelDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, EpisodeNovel)
