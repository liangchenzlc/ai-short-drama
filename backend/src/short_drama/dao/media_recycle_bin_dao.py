from sqlalchemy.orm import Session

from short_drama.domain import MediaRecycleBin

from .base import BaseDAO


class MediaRecycleBinDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, MediaRecycleBin)
