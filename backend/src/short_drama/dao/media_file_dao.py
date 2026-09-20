from sqlalchemy.orm import Session

from short_drama.domain import MediaFile

from .base import BaseDAO


class MediaFileDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, MediaFile)
