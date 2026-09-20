from short_drama.domain import Project
from short_drama.schemas import ProjectCreate, ProjectRead, ProjectUpdate

from .base import BaseService, utcnow


class ProjectService(BaseService):
    model = Project
    create_schema = ProjectCreate
    update_schema = ProjectUpdate
    read_schema = ProjectRead

    def open(self, identifier):
        with self._transaction():
            entity = self._get_locked(identifier)
            self.dao.update(entity, {"last_opened_at": utcnow()})
            return self._read(entity)
