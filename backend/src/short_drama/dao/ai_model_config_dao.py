from sqlalchemy.orm import Session

from short_drama.domain import AIModelConfig

from .base import BaseDAO


class AIModelConfigDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, AIModelConfig)
