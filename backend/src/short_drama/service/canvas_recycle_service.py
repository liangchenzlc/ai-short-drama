"""显式恢复本人回收站作品，保留原图、绘图、媒体和历史。"""

from copy import deepcopy

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.db.canvas_recycle_access import restoring_archived_canvas
from short_drama.service.base import utcnow
from short_drama.service.canvas_document import media_references
from short_drama.service.canvas_service import CanvasService, iso, validate_key


class CanvasRecycleService(CanvasService):
    def _archived_document(self, receipt, canvas):
        frozen = receipt.result_json.get("$archive_document")
        if isinstance(frozen, dict):
            return deepcopy(frozen)
        # Before this upgrade deletion receipts did not carry a frozen preview.
        # Reconstruct only this exact archived canvas, retaining private predicates.
        with restoring_archived_canvas(
            self.session, canvas.project_id, canvas.id, include_document=True
        ):
            document = self._document(canvas, private=True)
        document["revision"] = str(receipt.expected_row_version)
        return document

    def list_archived(self):
        with self._transaction(read_only=True):
            items = []
            for receipt in self.canvas_dao.recycle_receipts(self.actor_id):
                with restoring_archived_canvas(self.session, receipt.project_id, receipt.canvas_id):
                    project, canvas = self.canvas_dao.recycle_target(
                        receipt.project_id, receipt.canvas_id, lock=False
                    )
                    if (
                        project is None
                        or canvas is None
                        or project.workspace_mode != "infinite_canvas"
                    ):
                        continue
                    items.append(
                        {
                            "source_key": canvas.source_key,
                            "project_id": str(canvas.project_id),
                            "archive_key": receipt.idempotency_key,
                            "deleted_at": iso(receipt.created_at),
                            "source_document": self._archived_document(receipt, canvas),
                        }
                    )
            return {"items": items}

    def _disposal(self, receipt, *, lock=False):
        return self.canvas_dao.recycle_disposal(
            self.actor_id, receipt.canvas_id, receipt.committed_row_version, lock=lock
        )

    def _available(self, receipt, canvas, *, lock=False):
        if self._disposal(receipt, lock=lock):
            raise WorkflowError("canvas_recycle_purged", "作品已彻底删除，不能恢复", 410)
        self._check_version(canvas, receipt.committed_row_version)
        if canvas.archived_at is None:
            raise WorkflowError("canvas_not_archived", "画布已恢复，请刷新项目列表", 409)

    def read_resource(self, source_key, archive_key, resource_id, resources):
        with self._transaction(read_only=True):
            receipt = self.canvas_dao.receipt(self.actor_id, validate_key(archive_key))
            if receipt is None or receipt.operation_kind != "canvas.archive":
                raise NotFound("回收站记录不存在或已无权访问")
            with restoring_archived_canvas(self.session, receipt.project_id, receipt.canvas_id):
                project, canvas = self.canvas_dao.recycle_target(
                    receipt.project_id, receipt.canvas_id, lock=False
                )
                if project is None or canvas is None or canvas.source_key != source_key:
                    raise NotFound("回收站记录不存在或已无权访问")
                self._available(receipt, canvas)
                document = self._archived_document(receipt, canvas)
                if resource_id not in {value for _, value in media_references(document)}:
                    raise NotFound("资源不属于这条回收记录")
            with restoring_archived_canvas(
                self.session, receipt.project_id, receipt.canvas_id, resource_id=resource_id
            ):
                resource = resources.resources.resource(resource_id)
                if resource is None:
                    raise NotFound("回收站预览资源不存在或已无权访问")
                return resources.read_model(resource), resource.storage_locator

    def purge_archived(self, source_key: str, archive_key: str, key: str) -> dict:
        payload = {"source_key": source_key, "archive_key": archive_key}
        with self._transaction():
            archived = self.canvas_dao.receipt(self.actor_id, validate_key(archive_key))
            if archived is None or archived.operation_kind != "canvas.archive":
                raise NotFound("回收站记录不存在或已无权访问")
            with restoring_archived_canvas(
                self.session, archived.project_id, archived.canvas_id, disposal_receipt=True
            ):
                project, canvas = self.canvas_dao.recycle_target(
                    archived.project_id, archived.canvas_id
                )
                if project is None or canvas is None or canvas.source_key != source_key:
                    raise NotFound("回收站记录不存在或已无权访问")
                digest, replay = self.begin_write(key, "canvas.recycle.purge", payload, lock=True)
                if replay:
                    return replay.result_json
                disposed = self._disposal(archived, lock=True)
                if disposed:
                    return disposed.result_json
                self._available(archived, canvas, lock=True)
                result = {"source_key": source_key, "archive_key": archive_key, "state": "purged"}
                self.record_write(
                    key=key,
                    operation="canvas.recycle.purge",
                    digest=digest,
                    canvas=canvas,
                    result=result,
                    expected=archived.committed_row_version,
                )
                return result

    def archive_status(self, source_key: str, archive_key: str) -> dict:
        with self._transaction(read_only=True):
            receipt = self.canvas_dao.receipt(self.actor_id, validate_key(archive_key))
            if receipt is None or receipt.operation_kind != "canvas.archive":
                raise NotFound("回收站记录不存在或已无权访问")
            with restoring_archived_canvas(self.session, receipt.project_id, receipt.canvas_id):
                project, canvas = self.canvas_dao.recycle_target(
                    receipt.project_id, receipt.canvas_id, lock=False
                )
                if (
                    project is None
                    or project.workspace_mode != "infinite_canvas"
                    or canvas is None
                    or canvas.source_key != source_key
                ):
                    raise NotFound("回收站记录不存在或已无权访问")
                still_archived = (
                    canvas.archived_at is not None
                    and canvas.row_version == receipt.committed_row_version
                )
                return {
                    "source_key": canvas.source_key,
                    "project_id": str(project.id),
                    "archive_key": receipt.idempotency_key,
                    "expected_row_version": str(receipt.expected_row_version),
                    "committed_row_version": str(receipt.committed_row_version),
                    "state": "purged"
                    if self._disposal(receipt)
                    else "archived"
                    if still_archived
                    else "superseded",
                }

    def restore_archived(self, source_key: str, archive_key: str, key: str) -> dict:
        payload = {"source_key": source_key, "archive_key": archive_key}
        with self._transaction():
            archived = self.canvas_dao.receipt(self.actor_id, archive_key)
            if archived is None or archived.operation_kind != "canvas.archive":
                raise NotFound("回收站记录不存在或已无权访问")
            with restoring_archived_canvas(self.session, archived.project_id, archived.canvas_id):
                project, canvas = self.canvas_dao.recycle_target(
                    archived.project_id, archived.canvas_id
                )
                if (
                    project is None
                    or project.workspace_mode != "infinite_canvas"
                    or canvas is None
                    or canvas.source_key != source_key
                ):
                    raise NotFound("回收站记录不存在或已无权访问")
                if self._disposal(archived, lock=True):
                    raise WorkflowError("canvas_recycle_purged", "作品已彻底删除，不能恢复", 410)
                # A locking receipt read sees a concurrent restore that committed
                # while this request waited for the project lock.
                digest, receipt = self.begin_write(
                    key, "canvas.recycle.restore", payload, lock=True
                )
                if receipt:
                    return receipt.result_json
                self._check_version(canvas, archived.committed_row_version)
                if canvas.archived_at is None:
                    raise WorkflowError("canvas_not_archived", "画布已恢复，请刷新项目列表", 409)
                now = utcnow()
                canvas.archived_at = None
                canvas.row_version += 1
                canvas.updated_at, canvas.updated_by = now, self.actor_id
                if project.archived_at is not None:
                    project.archived_at = None
                    project.row_version += 1
                    project.updated_at = now
                # Restore the parent first. Ordinary scoped access then sees the
                # same private children and resources; no execution data is copied.
                self.session.flush()
            settings = self.canvas_dao.settings(project.id)
            if settings.primary_canvas_id is None:
                settings.primary_canvas_id = canvas.id
                settings.row_version += 1
                settings.updated_at, settings.updated_by = now, self.actor_id
            result = self.summary(canvas)
            self.record_write(
                key=key,
                operation="canvas.recycle.restore",
                digest=digest,
                canvas=canvas,
                result=result,
                expected=archived.committed_row_version,
            )
            self.session.flush()
            return result
