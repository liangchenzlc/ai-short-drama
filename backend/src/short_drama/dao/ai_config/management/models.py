from sqlalchemy.orm import Session

from short_drama.dao.base import BaseDAO
from short_drama.domain import AIModelConfig


class AIModelConfigDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, AIModelConfig)
