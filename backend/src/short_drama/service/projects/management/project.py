from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.projects.management.project import ProjectDAO
from short_drama.domain import Project
from short_drama.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.schemas.projects.management.creation import (
    ProjectCreateRequest,
    ProjectPatchRequest,
)
from short_drama.schemas.projects.management.project import ProjectSummary
from short_drama.service.base import BaseService, Page, utcnow

_UNSET = object()


class ProjectService(BaseService):
    model = Project
    create_schema = ProjectCreate
    update_schema = ProjectUpdate
    read_schema = ProjectRead

    def __init__(self, session, storage=None):
        super().__init__(session)
        self.storage = storage

    def _read(self, entity, *, canvas_summary=_UNSET, cover=_UNSET):
        dto = super()._read(entity)
        dto = dto.model_copy(update={"capabilities": {"edit": True}})
        if self.storage is not None and dto.workspace_mode == "standard":
            if cover is _UNSET:
                cover = ProjectDAO(self.session).covers([entity.id]).get(entity.id)
            if cover is not None:
                dto.cover_url = self.storage.download_url(cover.storage_locator)
        if dto.workspace_mode == "infinite_canvas":
            if canvas_summary is _UNSET:
                from short_drama.dao.canvas.document.canvas import CanvasDAO

                canvas_summary = CanvasDAO(self.session).summaries([entity.id]).get(entity.id, {})
            dto = dto.model_copy(update=canvas_summary)
        return dto

    def create_project(self, payload, *, idempotency_key=None):
        values = self._payload(ProjectCreateRequest, payload)
        with self._transaction():
            canvas_service = None
            if values.get("workspace_mode") == "infinite_canvas":
                from short_drama.service.canvas.document.canvas import CanvasService

                canvas_service = CanvasService(self.session)
                normalized = ProjectCreateRequest.model_validate(values).model_dump(mode="json")
                digest, receipt = canvas_service.begin_write(
                    idempotency_key, "project.create", normalized
                )
                if receipt:
                    return ProjectRead.model_validate(receipt.result_json)
            values = self._creation_audit(values)
            values["last_opened_at"] = values["created_at"]
            project = self.dao.create(values)
            from short_drama.core.config import Settings
            from short_drama.domain.creation.sound.voices.voices import ProjectSoundMode

            settings = Settings()
            if canvas_service:
                canvas = canvas_service.initialize_project(project)
                result = self._read(project)
                canvas_service.record_write(
                    key=idempotency_key,
                    operation="project.create",
                    digest=digest,
                    canvas=canvas,
                    result=result.model_dump(mode="json"),
                )
                return result
            if settings.native_video_enabled and settings.audio_production_enabled:
                self.session.add(
                    ProjectSoundMode(project_id=project.id, mode="native", row_version=1)
                )
                self.session.flush()
            return self._read(project)

    def list_projects(self, offset=0, limit=20, query=""):
        with self._transaction(read_only=True):
            rows, total = ProjectDAO(self.session).list_recent(offset, limit, query)
            from short_drama.dao.canvas.document.canvas import CanvasDAO

            summaries = CanvasDAO(self.session).summaries(
                [row.id for row in rows if row.workspace_mode == "infinite_canvas"]
            )
            covers = (
                ProjectDAO(self.session).covers(
                    [row.id for row in rows if row.workspace_mode == "standard"]
                )
                if self.storage is not None
                else {}
            )
            return Page(
                items=[
                    ProjectSummary(
                        **self._read(
                            row, canvas_summary=summaries.get(row.id, {}), cover=covers.get(row.id)
                        ).model_dump()
                    )
                    for row in rows
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
            if version != entity.row_version:
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
            self.dao.update(entity, {"last_opened_at": utcnow()})
            return self._read(entity)

    def delete_project(self, identifier: int | str, row_version: int | str) -> None:
        expected = parse_identifier(row_version)
        with self._transaction():
            entity = self._get_locked(identifier)
            if entity.archived_at is not None:
                raise NotFound("项目不存在或已删除")
            if expected != entity.row_version:
                raise WorkflowError("project_version_conflict", "项目已变化，请重新核对后删除", 409)
            if entity.row_version >= UINT64_MAX:
                raise Conflict("Project version exhausted")
            from short_drama.db.scope.resource_scope import require_project

            require_project(self.session, entity.id, owner=True)
            now = utcnow()
            if entity.workspace_mode == "infinite_canvas":
                for canvas in ProjectDAO(self.session).lock_all_canvases(entity.id):
                    if canvas.row_version >= UINT64_MAX:
                        raise Conflict("Canvas version exhausted")
                    canvas.archived_at = now
                    canvas.row_version += 1
                    canvas.updated_at = now
            self._apply_update(entity, {"archived_at": now, "row_version": entity.row_version + 1})
