from short_drama.dao.project_dao import ProjectDAO
from short_drama.domain import Project
from short_drama.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from short_drama.schemas.project import ProjectSummary
from short_drama.schemas.project_creation import ProjectCreateRequest, ProjectPatchRequest

from .base import BaseService, Page, utcnow


class ProjectService(BaseService):
    model = Project
    create_schema = ProjectCreate
    update_schema = ProjectUpdate
    read_schema = ProjectRead

    def create_project(self, payload):
        values = self._payload(ProjectCreateRequest, payload)
        with self._transaction():
            values = self._creation_audit(values)
            values["last_opened_at"] = values["created_at"]
            return self._read(self.dao.create(values))

    def list_projects(self, offset=0, limit=20, query=""):
        with self._transaction():
            rows, total = ProjectDAO(self.session).list_with_episode_counts(offset, limit, query)
            return Page(
                items=[
                    ProjectSummary(**self._read(row).model_dump(), episode_count=count)
                    for row, count in rows
                ],
                total=total,
                offset=offset,
                limit=limit,
            )

    def update_project(self, identifier, payload):
        values = self._payload(ProjectPatchRequest, payload)
        return self.update(identifier, values)

    def open(self, identifier):
        with self._transaction():
            entity = self._get_locked(identifier)
            self.dao.update(entity, {"last_opened_at": utcnow()})
            return self._read(entity)
