from __future__ import annotations

from copy import deepcopy

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_library_dao import CanvasLibraryDAO
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasLibraryAsset,
    CanvasLibraryAssetReference,
    CanvasLibraryFolder,
    CanvasLibraryFolderItem,
)
from short_drama.domain.canvas import (
    CanvasDirectorScene,
    CanvasEdge,
    CanvasNode,
    CanvasNodeUserState,
    CanvasTimeline,
    CanvasUserState,
)
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.canvas_library import (
    CanvasLibraryDocument,
    CanvasLibraryFolderRead,
    CanvasLibraryFolderWrite,
    CanvasLibraryMove,
    CanvasLibrarySummary,
)
from short_drama.utils.snowflake import next_id

from .base import BaseService, utcnow
from .canvas_document import media_references
from .canvas_library_references import canvas_media_bindings
from .canvas_service import CanvasService, iso


class CanvasLibraryService(BaseService):
    model = CanvasLibraryAsset
    # Serialize the private library without locking the users parent row. Canvas
    # autosave references that row while holding the project lock.
    global_ordered = True

    def __init__(self, session):
        super().__init__(session)
        self.library, self.resources = CanvasLibraryDAO(session), CanvasResourceDAO(session)
        self.canvases = CanvasService(session)

    @staticmethod
    def summary(row, folder_id=""):
        return CanvasLibrarySummary(
            id=row.source_key,
            kind=row.kind,
            title=row.title,
            category=row.category,
            status=row.status,
            folder_id=folder_id,
            created_at=iso(row.created_at),
            updated_at=iso(row.updated_at),
        )

    def read(self, key):
        with self._transaction(read_only=True):
            row = self.library.asset(key)
            if row is None:
                raise NotFound("Canvas library asset does not exist")
            return self.documents([row])[0]

    def documents(self, rows: list[CanvasLibraryAsset]) -> list[CanvasLibraryDocument]:
        folders = self.library.folder_items([row.id for row in rows])
        return [
            CanvasLibraryDocument.model_validate(
                {
                    **row.payload_json,
                    "folderId": str(folders[row.id].folder_id) if row.id in folders else "",
                    "createdAt": iso(row.created_at),
                    "updatedAt": iso(row.updated_at),
                }
            )
            for row in rows
        ]

    def list(self):
        with self._transaction(read_only=True):
            rows = self.library.assets()
            folders = self.library.folder_items([row.id for row in rows])
            return [
                self.summary(row, str(folders[row.id].folder_id) if row.id in folders else "")
                for row in rows
            ]

    def page(self, filters):
        with self._transaction(read_only=True):
            page = self.library.page(filters)
            page["assets"] = self.documents(page["assets"])
            return page

    def batch(self, keys):
        if any(not key.strip() or len(key) > 80 for key in keys):
            raise WorkflowError("canvas_asset_invalid", "素材 ID 无效", 422)
        with self._transaction(read_only=True):
            return self.documents(self.library.assets(keys))

    def delete_asset(self, key: str, expected_status: str = "") -> None:
        from .canvas_library_deletion import delete_library_asset

        delete_library_asset(self, key, expected_status)

    @staticmethod
    def folder_read(row: CanvasLibraryFolder) -> CanvasLibraryFolderRead:
        return CanvasLibraryFolderRead(
            id=row.id,
            name=row.name,
            position=row.position,
            created_at=iso(row.created_at),
            updated_at=iso(row.updated_at),
        )

    def folders(self) -> list[CanvasLibraryFolderRead]:
        with self._transaction(read_only=True):
            return [self.folder_read(row) for row in self.library.folders()]

    def require_folder(self, value: str | int) -> CanvasLibraryFolder:
        try:
            identifier = parse_identifier(value)
        except ValueError:
            raise WorkflowError("canvas_asset_folder_invalid", "素材分类 ID 无效", 422) from None
        folder = self.library.folder(identifier, lock=True)
        if folder is None:
            raise NotFound("Canvas library folder does not exist")
        return folder

    def save_folder(self, payload: CanvasLibraryFolderWrite, identifier: int | None = None):
        payload = CanvasLibraryFolderWrite.model_validate(payload.model_dump())
        with self._transaction():
            row = self.require_folder(identifier) if identifier else None
            name_key = payload.name.lower()
            existing = self.library.folder_named(name_key)
            if existing is not None and (row is None or existing.id != row.id):
                raise WorkflowError("canvas_asset_folder_exists", "已存在同名素材分类", 409)
            if row is None:
                row = CanvasLibraryFolder(
                    **self.canvases.audit(),
                    user_id=self.canvases.actor_id,
                    name=payload.name,
                    name_key=name_key,
                    position=self.library.next_folder_position(),
                )
                self.session.add(row)
            else:
                row.name, row.name_key, row.updated_at = payload.name, name_key, utcnow()
            self.session.flush()
            return self.folder_read(row)

    def delete_folder(self, identifier: int) -> None:
        with self._transaction():
            row = self.require_folder(identifier)
            for asset in self.library.assets_in_folder(row.id):
                asset.updated_at = utcnow()
            # Folder membership is canonical. FK cascades also unclassify currently
            # inaccessible project assets without reading or rewriting their payloads.
            self.session.delete(row)
            self.session.flush()

    def assign_folder(self, row: CanvasLibraryAsset, folder: CanvasLibraryFolder | None) -> bool:
        current = self.library.folder_items([row.id]).get(row.id)
        if (current.folder_id if current else None) == (folder.id if folder else None):
            return False
        if folder is None:
            if current is not None:
                self.session.delete(current)
        elif current is None:
            self.session.add(
                CanvasLibraryFolderItem(
                    id=next_id(),
                    library_asset_id=row.id,
                    folder_id=folder.id,
                )
            )
        else:
            current.folder_id = folder.id
        return True

    def move(self, payload: CanvasLibraryMove) -> CanvasLibraryMove:
        payload = CanvasLibraryMove.model_validate(payload.model_dump())
        with self._transaction():
            folder_id = payload.folder_id.strip()
            folder = self.require_folder(folder_id) if folder_id else None
            rows = self.library.assets(payload.asset_ids, lock=True)
            if len(rows) != len(payload.asset_ids):
                raise NotFound("Some canvas library assets do not exist")
            for row in rows:
                self.assign_folder(row, folder)
                row.updated_at = utcnow()
            self.session.flush()
            return payload.model_copy(update={"folder_id": str(folder.id) if folder else ""})

    def _guard_canvas_references(self, project_id: int, key: str, identifiers: set[int]) -> None:
        self.canvases.require_project(project_id, lock=True)
        dao = self.canvases.canvas_dao
        canvases = dao.canvases(project_id, lock=True)
        # Use current locking reads after the project lock, even if earlier scope
        # checks established an older MySQL REPEATABLE READ snapshot.
        children = dao.workspace_children(
            canvases,
            (
                CanvasNode,
                CanvasEdge,
                CanvasDirectorScene,
                CanvasTimeline,
                CanvasUserState,
                CanvasNodeUserState,
            ),
            lock=True,
        )
        for canvas in canvases:
            document = self.canvases._document(canvas, private=True, children=children[canvas.id])
            for asset_key, identifier in canvas_media_bindings(document):
                if asset_key == key and identifier not in identifiers:
                    raise WorkflowError(
                        "canvas_asset_resource_conflict",
                        "素材仍被画布引用，不能替换为其他资源",
                        409,
                    )

    def put(self, key, asset):
        asset = CanvasLibraryDocument.model_validate(asset.model_dump())
        if asset.id != key:
            raise WorkflowError("canvas_asset_invalid", "素材 ID 与路径不一致", 422)
        payload = asset.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude={"folder_id"}
        )
        category = asset.category.strip().lower()
        if category in {"wardrobe", "weapon", "accessory"}:
            category = "prop"
        elif category == "style":
            category = "material"
        elif category not in {"character", "environment", "prop", "material", "other"}:
            category = (
                "character"
                if asset.kind == "entity"
                else "other"
                if asset.kind == "text"
                else "material"
            )
        payload["category"] = category
        identifiers = {identifier for _, identifier in media_references(payload)}
        with self._transaction():
            folder_id = asset.folder_id.strip()
            folder = self.require_folder(folder_id) if folder_id else None
            resources = self.resources.resource_map(identifiers)
            if identifiers != resources.keys():
                raise NotFound("Canvas library resource does not exist")
            project_ids = {
                resource.project_id for resource in resources.values() if resource.project_id
            }
            source_canvas = asset.metadata.get("canvasId")
            if source_canvas:
                if not isinstance(source_canvas, str) or len(source_canvas) > 64:
                    raise WorkflowError("canvas_asset_invalid", "素材来源画布 ID 无效", 422)
                canvas = self.resources.canvas(source_canvas)
                if canvas is None:
                    raise NotFound("Canvas does not exist")
                project_ids.add(canvas.project_id)
            if len(project_ids) > 1:
                raise WorkflowError(
                    "canvas_asset_scope_conflict", "跨项目素材须先建立独立项目副本", 409
                )
            project_id = next(iter(project_ids), None)
            if project_id:
                self.canvases.require_project(project_id)
            row = self.library.asset(key, lock=True)
            if row is not None:
                if row.project_id != project_id:
                    raise WorkflowError(
                        "canvas_asset_scope_conflict", "素材归属不可更改，请创建独立副本", 409
                    )
                previous = self.library.references(row.id)
                previous_ids = {ref.media_id or ref.binary_id for ref in previous}
            else:
                row = CanvasLibraryAsset(
                    **self.canvases.audit(),
                    user_id=self.canvases.actor_id,
                    project_id=project_id,
                    source_key=key,
                    kind=asset.kind,
                    title=asset.title,
                    category=category,
                    status=asset.status,
                    payload_json={},
                )
                self.session.add(row)
                previous = []
                previous_ids = set()
            old_declared = {value for _, value in media_references(row.payload_json)}
            managed_copies = previous_ids - old_declared
            copy_resources = self.resources.resource_map(managed_copies)
            copy_ancestors = self.resources.copy_ancestors(set(copy_resources))
            kept_copies = {
                identifier
                for identifier, ancestors in copy_ancestors.items()
                if ancestors.intersection(identifiers)
            }
            guarded_projects = {
                resource.project_id for resource in copy_resources.values() if resource.project_id
            }
            if project_id:
                guarded_projects.add(project_id)
            for guarded_project in sorted(guarded_projects):
                self._guard_canvas_references(guarded_project, key, identifiers | kept_copies)
            # Private asset payloads keep their source identity. Extra references
            # retain project copies, including inaccessible copies after revocation;
            # losing target access cannot authorize deleting another project's file.
            identifiers |= kept_copies | (managed_copies - copy_resources.keys())
            payload["createdAt"], payload["updatedAt"] = iso(row.created_at), iso(row.updated_at)
            if row.payload_json != payload:
                row.updated_at = utcnow()
                payload["updatedAt"] = iso(row.updated_at)
                row.payload_json = deepcopy(payload)
                row.kind, row.title, row.category, row.status = (
                    asset.kind,
                    asset.title,
                    category,
                    asset.status,
                )
            if self.assign_folder(row, folder):
                row.updated_at = utcnow()
            for reference in previous:
                if (reference.media_id or reference.binary_id) not in identifiers:
                    self.session.delete(reference)
            for identifier in sorted(identifiers - previous_ids):
                binary = isinstance(resources[identifier], CanvasBinaryResource)
                self.session.add(
                    CanvasLibraryAssetReference(
                        id=next_id(),
                        library_asset_id=row.id,
                        media_id=None if binary else identifier,
                        binary_id=identifier if binary else None,
                    )
                )
            self.session.flush()
            return self.summary(row, str(folder.id) if folder else "")
