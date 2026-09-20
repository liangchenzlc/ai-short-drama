from sqlalchemy.orm import Session

from short_drama.domain import EpisodeScript

from .base import BaseDAO


class EpisodeScriptDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, EpisodeScript)
