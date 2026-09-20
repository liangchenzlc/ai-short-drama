from sqlalchemy.orm import Session

from short_drama.domain import Project

from .base import BaseDAO


class ProjectDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, Project)
