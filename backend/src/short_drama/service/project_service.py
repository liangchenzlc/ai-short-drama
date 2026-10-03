from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.project_dao import ProjectDAO
from short_drama.domain import Project
from short_drama.domain.collaboration import UserProjectState
from short_drama.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from short_drama.schemas.project import ProjectSummary
from short_drama.schemas.project_creation import ProjectCreateRequest, ProjectPatchRequest
from short_drama.utils.snowflake import next_id

from .base import BaseService, Page, utcnow

_UNSET = object()


class ProjectService(BaseService):
    model = Project
    create_schema = ProjectCreate
    update_schema = ProjectUpdate
    read_schema = ProjectRead

    def _read(self, entity, *, opened=_UNSET):
        dto = super()._read(entity)
        actor = self.session.info.get("actor")
        if actor:
            if opened is _UNSET:
                opened = self.session.scalar(
                    select(UserProjectState.last_opened_at).where(
                        UserProjectState.user_id == actor.user_id,
                        UserProjectState.project_id == entity.id,
                    )
                )
            dto = dto.model_copy(
                update={
                    "last_opened_at": opened,
                    "capabilities": {
                        "edit": True,
                        "manage_members": entity.owner_user_id == actor.user_id,
                        "delete": entity.owner_user_id == actor.user_id,
                        "leave": entity.owner_user_id != actor.user_id,
                    },
                }
            )
        return dto

    def delete(self, identifier):
        if not self.session.info.get("actor"):
            return super().delete(identifier)
        with self._transaction():
            from short_drama.db.access import require_project

            require_project(self.session, identifier, owner=True)
            entity = self._get_locked(identifier)
            entity.archived_at = utcnow()
            entity.row_version += 1
            self.session.flush()

    def create_project(self, payload):
        values = self._payload(ProjectCreateRequest, payload)
        with self._transaction():
            values = self._creation_audit(values)
            values["last_opened_at"] = values["created_at"]
            project = self.dao.create(values)
            from short_drama.core.config import Settings
            from short_drama.domain.native_voice import ProjectSoundMode

            settings = Settings()
            if settings.native_video_enabled and settings.audio_production_enabled:
                self.session.add(
                    ProjectSoundMode(project_id=project.id, mode="native", row_version=1)
                )
                self.session.flush()
            return self._read(project)

    def list_projects(self, offset=0, limit=20, query=""):
        with self._transaction(read_only=True):
            rows, total = ProjectDAO(self.session).list_with_episode_counts(offset, limit, query)
            return Page(
                items=[
                    ProjectSummary(
                        **self._read(row, opened=opened).model_dump(), episode_count=count
                    )
                    for row, count, opened in rows
                ],
                total=total,
                offset=offset,
                limit=limit,
            )

    def update_project(self, identifier, payload):
        values = self._payload(ProjectPatchRequest, payload)
        version = values.pop("row_version", None)
        with self._transaction():
            entity = self._get_locked(identifier)
            if self.session.info.get("actor") and version != entity.row_version:
                raise WorkflowError(
                    "project_version_conflict",
                    "Project settings changed; reload and merge your edits",
                    409,
                )
            if any(getattr(entity, k) != v for k, v in values.items()):
                values["row_version"] = entity.row_version + 1
            return self._read(self._apply_update(entity, values))

    def open(self, identifier):
        with self._transaction():
            entity = self._get_locked(identifier)
            actor = self.session.info.get("actor")
            if actor:
                state = self.session.scalar(
                    select(UserProjectState)
                    .where(
                        UserProjectState.user_id == actor.user_id,
                        UserProjectState.project_id == entity.id,
                    )
                    .with_for_update()
                )
                if state:
                    state.last_opened_at = utcnow()
                else:
                    self.session.add(
                        UserProjectState(
                            id=next_id(),
                            user_id=actor.user_id,
                            project_id=entity.id,
                            last_opened_at=utcnow(),
                        )
                    )
                self.session.flush()
            else:
                self.dao.update(entity, {"last_opened_at": utcnow()})
            return self._read(entity)
