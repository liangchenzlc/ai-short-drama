from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.domain import Episode, Project

from .base import BaseDAO


class ProjectDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, Project)

    def list_with_episode_counts(self, offset, limit, query):
        self.validate_pagination(offset, limit)
        conditions = [Project.name.contains(query, autoescape=True)] if query else []
        total = self.session.scalar(select(func.count()).select_from(Project).where(*conditions))
        episode_count = (
            select(func.count())
            .select_from(Episode)
            .where(Episode.project_id == Project.id)
            .correlate(Project)
            .scalar_subquery()
        )
        rows = self.session.execute(
            select(Project, episode_count)
            .where(*conditions)
            .order_by(Project.last_opened_at.desc(), Project.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return rows, total
