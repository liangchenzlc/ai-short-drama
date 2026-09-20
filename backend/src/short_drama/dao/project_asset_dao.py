from sqlalchemy.orm import Session

from short_drama.domain import ProjectAsset

from .base import BaseDAO


class ProjectAssetDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, ProjectAsset)
