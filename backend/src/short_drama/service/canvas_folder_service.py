"""原版项目文件夹的私人归属、封面保护与不可复活的删除。"""

from datetime import UTC, datetime

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_folder_dao import CanvasFolderDAO
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.domain import CanvasBinaryResource
from short_drama.domain.canvas_folder import CanvasProjectFolder
from short_drama.schemas.canvas_folder import CanvasFolderRead, CanvasFolderWrite

from .base import BaseService, utcnow
from .canvas_folder_state import canvas_folder_write_lock
from .canvas_service import CanvasService, iso


def client_created_at(value: str | None, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value or "")
        if parsed.tzinfo is not None and parsed.year >= 1000:
            return parsed.astimezone(UTC).replace(tzinfo=None)
    except ValueError:
        pass
    return fallback


class CanvasFolderService(BaseService):
    model = CanvasProjectFolder

    def __init__(self, session):
        super().__init__(session)
        self.folders = CanvasFolderDAO(session)
        self.canvases = CanvasService(session)

    @staticmethod
    def read(row: CanvasProjectFolder) -> CanvasFolderRead:
        return CanvasFolderRead(
            id=row.source_key,
            name=row.name,
            cover_resource_id=row.cover_media_id or row.cover_binary_id,
            created_at=iso(row.created_at),
            updated_at=iso(row.updated_at),
        )

    def list(self):
        with self._transaction(read_only=True):
            return [self.read(row) for row in self.folders.folders()]

    def put(self, key: str, payload: CanvasFolderWrite):
        payload = CanvasFolderWrite.model_validate(payload.model_dump())
        if payload.id is not None and payload.id != key:
            raise WorkflowError("canvas_folder_invalid", "文件夹 ID 与路径不一致", 422)
        with canvas_folder_write_lock(self.session), self._transaction():
            # Resource removal locks resource -> references. Take the cover lock
            # before the folder row to keep this same order and pin real bytes.
            resource = None
            if payload.cover_resource_id is not None:
                resource = CanvasResourceDAO(self.session).resource(
                    payload.cover_resource_id, lock=True
                )
                if resource is None or resource.created_by != self.canvases.actor_id:
                    raise NotFound("Canvas folder cover does not exist")
            row = self.folders.folder(key, lock=True)
            if row is not None and row.tombstoned_at is not None:
                raise WorkflowError("canvas_folder_deleted", "文件夹已删除，不能恢复旧修改", 409)
            if row is None:
                row = CanvasProjectFolder(
                    **self.canvases.audit(),
                    user_id=self.canvases.actor_id,
                    source_key=key,
                    name=payload.name,
                    tombstoned_at=None,
                )
                row.created_at = client_created_at(payload.created_at, row.created_at)
                self.session.add(row)
            row.name, row.updated_at = payload.name, utcnow()
            row.cover_media_id = (
                resource.id if resource and not isinstance(resource, CanvasBinaryResource) else None
            )
            row.cover_binary_id = (
                resource.id if isinstance(resource, CanvasBinaryResource) else None
            )
            self.session.flush()
            return self.read(row)

    def delete(self, key: str):
        with canvas_folder_write_lock(self.session), self._transaction():
            row = self.folders.folder(key)
            if row is None:
                raise NotFound("Canvas folder does not exist")
            if row.tombstoned_at is not None:
                return
            items = self.folders.items(key)
            # Classification is private even when membership was revoked. Only
            # accessible works get history; every owned classification is cleared.
            targets = self.folders.targets([item.canvas_id for item in items])
            for project_id, canvas_id in targets:
                project = self.canvases.canvas_dao.project(project_id, lock=True)
                if project is None or project.archived_at is not None:
                    continue
                live = self.canvases.canvas_dao.canvas(project_id, canvas_id, lock=True)
                if live is None:
                    continue
                self.canvases._snapshot(
                    live, self.canvases._shared_document(live, lock=True), reason="automatic"
                )
                live.row_version += 1
                live.updated_at = utcnow()
            for item in items:
                self.session.delete(item)
            # History may reference media; acquire this row only after history
            # references have been flushed, avoiding cover-cleanup lock inversion.
            self.session.flush()
            row = self.folders.folder(key, lock=True)
            row.cover_media_id = row.cover_binary_id = None
            row.tombstoned_at = row.updated_at = utcnow()
            self.session.flush()
