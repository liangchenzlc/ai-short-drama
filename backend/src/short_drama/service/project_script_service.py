from sqlalchemy import select

from short_drama.dao.project_writing_dao import VersionedDocumentDAO
from short_drama.domain import Project, ProjectScript
from short_drama.schemas.project_script import (
    ProjectScriptCreate,
    ProjectScriptRead,
    ProjectScriptUpdate,
)

from .base import BaseService


class ProjectScriptService(BaseService):
    model = ProjectScript
    create_schema = ProjectScriptCreate
    update_schema = ProjectScriptUpdate
    read_schema = ProjectScriptRead
    parent_model = Project
    parent_field = "project_id"

    def __init__(self, session):
        super().__init__(session)
        self.dao = VersionedDocumentDAO(session, self.model)

    def _validate_update(self, entity, values):
        if "content" in values:
            values["editor_document"] = None
        if "content" in values and values["content"] != entity.content:
            values["state"] = "unconfirmed"

    def _apply_update(self, entity, values):
        creation = self.session.get(Project, entity.project_id)
        if "content" in values and creation.editing_script_id == entity.id:
            from .project_writing_service import ProjectWritingService

            writing = ProjectWritingService(self.session)
            writing.dao.require_idle(creation.id, "script")
            if "position" in values:
                entity.position = values["position"]
            result = writing.write_script(creation, entity, values["content"])
            writing._changed(creation)
            return result
        return super()._apply_update(entity, values)

    def confirm(self, identifier):
        """Confirm under the creation lock, clearing the previous confirmation first."""
        with self._transaction():
            target = self._get_locked(identifier)
            confirmed = list(
                self.session.scalars(
                    select(self.model)
                    .where(
                        self.model.project_id == target.project_id, self.model.state == "confirmed"
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            for row in confirmed:
                if row.id != target.id:
                    self._apply_update(row, {"state": "unconfirmed"})
            return self._read(self._apply_update(target, {"state": "confirmed"}))
