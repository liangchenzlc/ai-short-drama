from sqlalchemy.orm import Session

from short_drama.domain import ShotScript

from .base import BaseDAO


class ShotScriptDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, ShotScript)
