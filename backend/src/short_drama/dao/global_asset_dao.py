from sqlalchemy.orm import Session

from short_drama.domain import GlobalAsset

from .base import BaseDAO


class GlobalAssetDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, GlobalAsset)
