from uuid import uuid4

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.scope.resource_scope import require_project as require_project_access
from short_drama.domain import Project, ProjectCanvas, ProjectCanvasSettings
from short_drama.schemas.canvas.document.canvas import (
    CanvasCreateRequest,
)
from short_drama.schemas.canvas.workspace.creation.creation import canvas_creation_payload
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.audit import iso as iso
from short_drama.service.canvas.document.base import CanvasServiceBase
from short_drama.service.canvas.document.document import (
    split_document,
)
from short_drama.service.canvas.workspace.folders.folder_state import canvas_folder_write_lock


class CanvasCreation(CanvasServiceBase):
    def require_creation_result(self, result: dict):
        return self.require_canvas(int(result["project_id"]), int(result["id"]), lock=True)

    def validate_creation_source(
        self, request, *, project_id: int | None = None, extra_project_ids: set[int] | None = None
    ) -> dict | None:
        parent = (
            self.canvas_dao.source_canvas(request.source_canvas_key)
            if request.source_canvas_key is not None
            else None
        )
        if request.source_canvas_key is not None and parent is None:
            raise NotFound("Source canvas does not exist")
        projects = set(extra_project_ids or ())
        if project_id is not None:
            projects.add(project_id)
        if parent is not None:
            identity = (parent.id, parent.project_id)
            projects.add(parent.project_id)
        for identifier in sorted(projects):
            require_project_access(self.session, identifier, lock=True)
            if identifier == project_id or parent is not None and identifier == parent.project_id:
                self.require_project(identifier, lock=True)
        if parent is None:
            return None
        parent = self.canvas_dao.source_canvas(request.source_canvas_key, lock=True)
        if parent is None or (parent.id, parent.project_id) != identity:
            raise NotFound("Source canvas does not exist")
        self._check_version(parent, int(request.source_canvas_row_version))
        return {
            "canvas_id": str(parent.id),
            "owning_project_id": str(parent.project_id),
            "source_canvas_key": parent.source_key,
            "source_canvas_row_version": str(parent.row_version),
        }

    def initialize_project(self, project, *, source_key=None, title="未命名画布"):
        """Called inside the owning ProjectService transaction."""
        canvas = ProjectCanvas(
            **self.audit(),
            project_id=project.id,
            source_key=source_key or uuid4().hex,
            title=title,
            position=0,
            schema_version=1,
            row_version=1,
            properties_json={},
            archived_at=None,
        )
        self.session.add(canvas)
        self.session.flush()
        self.session.add(
            ProjectCanvasSettings(
                **self.audit(),
                project_id=project.id,
                primary_canvas_id=canvas.id,
                workspace_key=str(project.id),
                row_version=1,
            )
        )
        self.session.flush()
        return canvas

    def create_for_project(self, project_id, payload, key):
        request = CanvasCreateRequest.model_validate(
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, CanvasCreateRequest)
            else payload
        )
        with (
            self._copy_binding_lock(request.source_document),
            canvas_folder_write_lock(self.session),
            self._transaction(lock_request_project=False),
        ):
            return self.create_in_transaction(request, key, project_id=project_id)

    def create_workspace(self, payload: CanvasCreateRequest, key: str) -> dict:
        """Source new-project / duplicate actions atomically create their host owner."""
        with (
            self._copy_binding_lock(payload.source_document),
            canvas_folder_write_lock(self.session),
            self._transaction(lock_request_project=False),
        ):
            return self.create_in_transaction(payload, key)

    def create_in_transaction(
        self, request, key, *, project_id=None, source_key=None, prepare_document=None
    ):
        """Creation coordinator owns the transaction and any prepared file attachment."""
        operation = "canvas.create" if project_id is not None else "canvas.workspace.create"
        original = canvas_creation_payload(request)
        if project_id is not None:
            original = {"project_id": str(project_id), **original}
        digest, receipt = self.begin_write(key, operation, original)
        if receipt:
            self.require_creation_result(receipt.result_json)
            return receipt.result_json
        self.validate_creation_source(request, project_id=project_id)
        document = request.source_document
        if project_id is not None:
            existing = self.canvas_dao.canvases(project_id)
            canvas = ProjectCanvas(
                **self.audit(),
                project_id=project_id,
                source_key=source_key or request.source_key or uuid4().hex,
                title=request.title,
                position=max((x.position for x in existing), default=-1) + 1,
                schema_version=1,
                row_version=1,
                properties_json={},
                archived_at=None,
            )
            self.session.add(canvas)
            self.session.flush()
        else:
            if document and document.workspace_project_id not in {None, document.id}:
                raise WorkflowError(
                    "canvas_identity_mismatch", "Use the existing project route", 422
                )
            project = Project(
                **self.audit(),
                owner_user_id=self.actor_id,
                name=request.title[:120],
                workspace_mode="infinite_canvas",
                last_opened_at=utcnow(),
                row_version=1,
            )
            self.session.add(project)
            self.session.flush()
            canvas = self.initialize_project(
                project,
                source_key=source_key or request.source_key,
                title=request.title,
            )
            if document:
                document = document.model_copy(update={"workspace_project_id": str(project.id)})
        extra = {}
        if prepare_document is not None:
            document, extra = prepare_document(canvas, document)
        if document is not None:
            self._validate_identity(canvas, document)
            self._bind_library_copies(document)
            shared, private = split_document(document)
            self._replace_graph(canvas, shared)
            self._save_private(canvas, private)
        result = {**self.summary(canvas), **extra}
        self.record_write(key=key, operation=operation, digest=digest, canvas=canvas, result=result)
        return result
