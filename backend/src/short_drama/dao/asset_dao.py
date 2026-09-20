from sqlalchemy.orm import Session

from short_drama.domain import Asset

from .base import BaseDAO


class AssetDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, Asset)
