from sqlalchemy.orm import Session

from short_drama.domain import ScriptShotRecord

from .base import BaseDAO


class ScriptShotRecordDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, ScriptShotRecord)
