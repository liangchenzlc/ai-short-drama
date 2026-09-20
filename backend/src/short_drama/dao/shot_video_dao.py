from sqlalchemy.orm import Session

from short_drama.domain import ShotVideo

from .base import BaseDAO


class ShotVideoDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, ShotVideo)
