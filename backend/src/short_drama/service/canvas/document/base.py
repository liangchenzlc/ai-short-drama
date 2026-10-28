from copy import deepcopy

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas.document.canvas import CanvasDAO
from short_drama.db.scope.canvas_scope import require_canvas, require_canvas_project
from short_drama.domain import ProjectCanvas
from short_drama.domain.canvas.document.canvas import (
    CanvasWriteReceipt,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.audit import CanvasAuditService
from short_drama.service.canvas.document.audit import iso as iso
from short_drama.service.canvas.document.document import (
    content_hash,
)
from short_drama.service.canvas.document.support import validate_key
from short_drama.utils.snowflake import next_id


class CanvasServiceBase(CanvasAuditService):
    model = ProjectCanvas

    def __init__(self, session):
        super().__init__(session)
        self.canvas_dao = CanvasDAO(session)

    def require_project(self, project_id: int, *, lock=False):
        return require_canvas_project(self.session, project_id, lock=lock)

    def require_canvas(self, project_id: int, canvas_id: int, *, lock=False, refresh=False):
        return require_canvas(self.session, project_id, canvas_id, lock=lock, refresh=refresh)

    def lock_existing_canvases(self, canvases):
        for project_id in sorted({canvas.project_id for canvas in canvases}):
            self.require_project(project_id, lock=True)
        for original in sorted(canvases, key=lambda item: (item.project_id, item.id)):
            if self.canvas_dao.canvas(original.project_id, original.id, lock=True) is None:
                raise NotFound("Canvas does not exist")

    def begin_write(self, key: str, operation: str, payload: dict, *, lock: bool = False):
        validate_key(key)
        if (
            operation in {"project.create", "canvas.workspace.create"}
            and self.canvas_dao.lock_actor(self.actor_id) is None
        ):
            raise NotFound("Account does not exist")
        digest = content_hash({"operation": operation, "payload": payload})
        receipt = self.canvas_dao.receipt(self.actor_id, key, lock=lock)
        if receipt:
            if receipt.operation_kind != operation or receipt.request_hash != digest:
                raise WorkflowError(
                    "canvas_idempotency_conflict", "The key belongs to another request", 409
                )
            # The scoped query rechecks membership even when the target was archived.
        return digest, receipt

    def record_write(self, *, key, operation, digest, canvas, result, expected=None):
        self.session.add(
            CanvasWriteReceipt(
                id=next_id(),
                actor_user_id=self.actor_id,
                project_id=canvas.project_id,
                canvas_id=canvas.id,
                idempotency_key=key,
                operation_kind=operation,
                request_hash=digest,
                expected_row_version=expected,
                committed_row_version=canvas.row_version,
                result_json=deepcopy(result),
                created_at=utcnow(),
            )
        )
        self.session.flush()

    def read_receipt(self, key):
        with self._transaction(read_only=True):
            receipt = self.canvas_dao.receipt(self.actor_id, validate_key(key))
            if receipt is None:
                raise NotFound("Write receipt does not exist")
            return {
                "operation_kind": receipt.operation_kind,
                "result": self.public_receipt_result(receipt),
            }

    @staticmethod
    def public_receipt_result(receipt):
        return {
            key: value for key, value in receipt.result_json.items() if key != "$archive_document"
        }
