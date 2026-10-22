"""Project-scoped reads and versioned internal document writes."""

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.domain import Project, ProjectNovel, ProjectScript
from short_drama.schemas.base import UINT64_MAX

from .base import BaseDAO


def bump_writing_version(creation):
    if creation.content_version >= UINT64_MAX:
        raise Conflict("Writing version exhausted")
    creation.content_version += 1


class ProjectWritingDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, Project)

    def scoped_project(self, project_id, *, for_update=True):
        # All document writers share this mutex, including first insert/confirmation.
        statement = (
            select(Project)
            .where(Project.id == project_id, Project.workspace_mode == "standard")
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        row = self.session.scalar(statement)
        if row is None:
            raise NotFound("Project does not exist in this project")
        return row

    def novel(self, project_id, *, for_update=True):
        statement = (
            select(ProjectNovel)
            .where(ProjectNovel.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def script(self, project_id, script_id, *, for_update=True):
        statement = (
            select(ProjectScript)
            .where(ProjectScript.id == script_id, ProjectScript.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        row = self.session.scalar(statement)
        if row is None:
            raise NotFound("Script does not exist in this creation")
        return row

    def confirmed(self, project_id, *, for_update=True):
        statement = (
            select(ProjectScript)
            .where(ProjectScript.project_id == project_id, ProjectScript.state == "confirmed")
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def next_position(self, project_id):
        maximum = (
            self.session.scalar(
                select(ProjectScript.position)
                .where(ProjectScript.project_id == project_id)
                .order_by(ProjectScript.position.desc())
                .limit(1)
                .with_for_update()
            )
            or 0
        )
        if maximum == 2**32 - 1:
            raise Conflict("Script ordering space exhausted")
        return maximum + 1


class VersionedDocumentDAO(BaseDAO):
    """Internal CRUD/generation services also invalidate open editor snapshots.

    Services own the enclosing transaction and lock creation before child records.
    Direct writing API uses BaseDAO and increments once for its aggregate mutation.
    """

    def _creation(self, project_id):
        creation = BaseDAO(self.session, Project).get(project_id, for_update=True)
        if creation is None:
            raise NotFound("Project does not exist")
        return creation

    def create(self, values):
        creation = self._creation(values["project_id"])
        row = super().create(values)
        if self.model is ProjectScript and creation.editing_script_id is None:
            creation.editing_script_id = row.id
        bump_writing_version(creation)
        self.session.flush()
        return row

    def update(self, entity, values):
        creation = self._creation(entity.project_id)
        changed = any(getattr(entity, key) != value for key, value in values.items())
        row = super().update(entity, values)
        if changed:
            bump_writing_version(creation)
            self.session.flush()
        return row

    def delete(self, entity):
        creation = self._creation(entity.project_id)
        if self.model is ProjectScript and creation.editing_script_id == entity.id:
            creation.editing_script_id = None
        bump_writing_version(creation)
        super().delete(entity)
