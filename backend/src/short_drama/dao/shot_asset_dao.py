from sqlalchemy.orm import Session

from short_drama.domain import ShotAsset

from .base import BaseDAO


class ShotAssetDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, ShotAsset)
