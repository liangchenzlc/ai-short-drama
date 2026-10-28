import json
from copy import deepcopy
from datetime import timedelta

from sqlalchemy import select

from short_drama.core.exceptions import NotFound
from short_drama.dao.canvas.drawing.drawing import CanvasDrawingDAO
from short_drama.dao.canvas.resources.resource import CanvasResourceDAO
from short_drama.domain.canvas.document.canvas import (
    CanvasRevision,
    CanvasRevisionMediaReference,
    CanvasRevisionUserState,
    CanvasUserMediaReference,
)
from short_drama.domain.canvas.resources.resource import (
    CanvasBinaryReference,
    CanvasBinaryResource,
    CanvasUserBinaryReference,
)
from short_drama.schemas.canvas.document.canvas import (
    CanvasDocument,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.audit import iso as iso
from short_drama.service.canvas.document.base import CanvasServiceBase
from short_drama.service.canvas.document.document import (
    content_hash,
    media_references,
    project_document,
)
from short_drama.service.canvas.drawing.drawing_history import (
    bind_revision_drawings,
    check_drawing_heads,
    freeze_drawings,
    restore_revision_drawings,
)
from short_drama.service.canvas.resources.media_locators import canonicalize_canvas_media
from short_drama.service.canvas.workspace.folders.folder_state import (
    canvas_folder_write_lock,
)


class CanvasRevisions(CanvasServiceBase):
    def _snapshot(self, canvas, before, *, reason):
        self._check_legacy_skill_body(before)
        before = canonicalize_canvas_media(before, strict=False)
        revisions = self.canvas_dao.snapshot_revisions(canvas.id)
        if revisions and (
            revisions[0].canvas_row_version == canvas.row_version
            or (
                reason != "before_restore"
                and revisions[0].created_at > utcnow() - timedelta(minutes=5)
            )
        ):
            return
        # 完整权限表达式的编译缓存键遍历会主导快照开销；此事务采用直接编译。
        # 连接选项随事务结束释放，全部权限查询与行锁仍按原路径执行。
        self.session.connection().execution_options(compiled_cache=None)
        before, drawing_versions = freeze_drawings(
            self, canvas.id, before, lock=True, current=reason == "before_restore"
        )
        revision = CanvasRevision(
            **self.child(canvas),
            canvas_row_version=canvas.row_version,
            schema_version=canvas.schema_version,
            snapshot_json=deepcopy(before),
            content_hash=content_hash(before),
            reason=reason,
        )
        self.session.add(revision)
        self.session.flush()
        bind_revision_drawings(self, canvas, revision, drawing_versions)
        private = self._private_state(canvas, lock=True)
        self.session.add(
            CanvasRevisionUserState(
                **self.child(canvas),
                user_id=self.actor_id,
                revision_id=revision.id,
                state_json=private,
            )
        )
        private_references = list(media_references(private))
        shared_identifiers = {value for _, value in media_references(before)}
        resources = CanvasResourceDAO(self.session).resource_map(
            shared_identifiers | {value for _, value in private_references}
        )
        for path, identifier in private_references:
            resource = resources.get(identifier)
            binary = isinstance(resource, CanvasBinaryResource)
            model = CanvasUserBinaryReference if binary else CanvasUserMediaReference
            self.session.add(
                model(
                    **self.child(canvas),
                    user_id=self.actor_id,
                    revision_id=revision.id,
                    owner_kind="revision",
                    owner_key=str(revision.id),
                    slot=content_hash(path),
                    ordinal=0,
                    **{"binary_id" if binary else "media_id": identifier},
                )
            )
        for identifier in sorted(shared_identifiers):
            resource = resources.get(identifier)
            if isinstance(resource, CanvasBinaryResource):
                self.session.add(
                    CanvasBinaryReference(
                        **self.child(canvas),
                        revision_id=revision.id,
                        binary_id=identifier,
                        owner_kind="revision",
                        owner_key=str(revision.id),
                        slot=str(identifier),
                        ordinal=0,
                    )
                )
                continue
            self.session.add(
                CanvasRevisionMediaReference(
                    **self.child(canvas), revision_id=revision.id, media_id=identifier
                )
            )
        self.session.flush()
        for expired in revisions[19:]:
            # The revision owns its reference and private-projection rows (database CASCADE).
            self.session.delete(expired)

    def list_revisions(self, project_id, canvas_id):
        with self._transaction(read_only=True):
            canvas = self.require_canvas(project_id, canvas_id)
            rows = sorted(
                self.canvas_dao.children(CanvasRevision, canvas_id),
                key=lambda x: x.canvas_row_version,
                reverse=True,
            )
            return {
                "items": [self._revision_summary(x) for x in rows],
                "row_version": str(canvas.row_version),
                "drawing_heads": CanvasDrawingDAO(self.session).heads(canvas_id),
            }

    @staticmethod
    def _revision_summary(revision):
        document = revision.snapshot_json
        return {
            "id": str(revision.id),
            "canvas_id": str(revision.canvas_id),
            "row_version": str(revision.canvas_row_version),
            "title": document["title"],
            "node_count": len(document.get("nodes", [])),
            "connection_count": len(document.get("connections", [])),
            "payload_bytes": len(
                json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
            ),
            "reason": revision.reason,
            "created_at": iso(revision.created_at),
            "content_updated_at": document["updatedAt"],
        }

    def _revision(self, canvas_id, revision_id):
        row = self.session.scalar(
            select(CanvasRevision).where(
                CanvasRevision.canvas_id == canvas_id, CanvasRevision.id == revision_id
            )
        )
        if row is None:
            raise NotFound("Canvas history does not exist or has expired")
        return row

    def _revision_document(self, revision, canvas):
        private = self.session.scalar(
            select(CanvasRevisionUserState).where(
                CanvasRevisionUserState.revision_id == revision.id
            )
        )
        shared = deepcopy(revision.snapshot_json)
        shared.pop("folderId", None)
        document = canonicalize_canvas_media(
            project_document(shared, private.state_json if private else None),
            strict=False,
        )
        return freeze_drawings(self, revision.canvas_id, document)[0]

    def read_revision(self, project_id, canvas_id, revision_id):
        with self._transaction(read_only=True):
            canvas = self.require_canvas(project_id, canvas_id)
            revision = self._revision(canvas_id, revision_id)
            document = self._revision_document(revision, canvas)
            return {
                "revision": self._revision_summary(revision),
                "source_document": document,
                "resource_aliases": self._resource_aliases(document),
            }

    def restore(
        self, project_id, canvas_id, revision_id, expected, key, expected_drawing_heads=None
    ):
        with (
            canvas_folder_write_lock(self.session),
            self._transaction(lock_request_project=False),
        ):
            original = self.require_canvas(project_id, canvas_id)
            self._revision(original.id, revision_id)
            canvas = self.require_canvas(
                project_id,
                canvas_id,
                lock=True,
            )
            payload = {
                "project_id": str(project_id),
                "canvas_id": str(canvas_id),
                "revision_id": str(revision_id),
                "expected": str(expected),
            }
            if expected_drawing_heads is not None:
                payload["expected_drawing_heads"] = expected_drawing_heads
            digest, receipt = self.begin_write(
                key,
                "canvas.restore",
                payload,
            )
            if receipt:
                return receipt.result_json
            self._check_version(canvas, expected)
            check_drawing_heads(self, canvas, expected_drawing_heads)
            revision = self._revision(canvas_id, revision_id)
            self._check_legacy_skill_body(revision.snapshot_json)
            document = self._revision_document(revision, canvas)
            self._snapshot(
                canvas, self._shared_document(canvas, lock=True), reason="before_restore"
            )
            restore_revision_drawings(self, canvas, revision, document)
            document.update(
                id=canvas.source_key,
                workspaceProjectId=str(project_id),
                revision=str(canvas.row_version),
            )
            self._apply_document(
                canvas, CanvasDocument.model_validate(document), reason="before_restore"
            )
            result = self.summary(canvas)
            self.record_write(
                key=key,
                operation="canvas.restore",
                digest=digest,
                canvas=canvas,
                result=result,
                expected=expected,
            )
            return result

    def archive(self, project_id, canvas_id, expected, key):
        with self._transaction():
            digest, receipt = self.begin_write(
                key,
                "canvas.archive",
                {
                    "project_id": str(project_id),
                    "canvas_id": str(canvas_id),
                    "expected": str(expected),
                },
            )
            if receipt:
                return self.public_receipt_result(receipt)
            project = self.require_project(project_id, lock=True)
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
            self._check_version(canvas, expected)
            archived_document = self._document(canvas, private=True)
            canvas.archived_at, canvas.updated_at = utcnow(), utcnow()
            canvas.row_version += 1
            remaining = [x for x in self.canvas_dao.canvases(project_id) if x.id != canvas.id]
            settings = self.canvas_dao.settings(project_id)
            if settings.primary_canvas_id == canvas.id:
                settings.primary_canvas_id = remaining[0].id if remaining else None
                settings.row_version += 1
                settings.updated_at = utcnow()
            result = {
                "archived_canvas_id": str(canvas.id),
                "next_canvas_id": str(remaining[0].id) if remaining else None,
                "project_archived": not remaining,
            }
            self.record_write(
                key=key,
                operation="canvas.archive",
                digest=digest,
                canvas=canvas,
                result={**result, "$archive_document": archived_document},
                expected=expected,
            )
            if not remaining:
                project.archived_at = utcnow()
                project.row_version += 1
            self.session.flush()
            return result
