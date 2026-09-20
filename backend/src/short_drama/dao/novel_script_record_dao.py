from sqlalchemy.orm import Session

from short_drama.domain import NovelScriptRecord

from .base import BaseDAO


class NovelScriptRecordDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, NovelScriptRecord)
