from sqlalchemy.orm import Session

from short_drama.domain import EpisodeAsset

from .base import BaseDAO


class EpisodeAssetDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, EpisodeAsset)
