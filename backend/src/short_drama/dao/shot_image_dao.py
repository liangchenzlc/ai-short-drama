from sqlalchemy.orm import Session

from short_drama.domain import ShotImage

from .base import BaseDAO


class ShotImageDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, ShotImage)
