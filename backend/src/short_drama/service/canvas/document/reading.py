from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import NotFound
from short_drama.dao.canvas.resources.resource import CanvasResourceDAO
from short_drama.domain import ProjectCanvas
from short_drama.domain.canvas.document.canvas import (
    CanvasDirectorScene,
    CanvasEdge,
    CanvasNode,
    CanvasNodeUserState,
    CanvasTimeline,
    CanvasUserState,
)
from short_drama.domain.canvas.workspace.folders.folder import CanvasProjectFolderItem
from short_drama.service.canvas.document.audit import iso as iso
from short_drama.service.canvas.document.base import CanvasServiceBase
from short_drama.service.canvas.document.document import (
    media_references,
    project_document,
)
from short_drama.service.canvas.resources.media_locators import canonicalize_canvas_media


class CanvasReading(CanvasServiceBase):
    def summary(self, canvas):
        return {
            "id": str(canvas.id),
            "project_id": str(canvas.project_id),
            "source_key": canvas.source_key,
            "title": canvas.title,
            "row_version": str(canvas.row_version),
            "schema_version": canvas.schema_version,
            "created_at": iso(canvas.created_at),
            "updated_at": iso(canvas.updated_at),
        }

    def _shared_document(self, canvas, *, children=None, lock=False, refresh=False):
        def rows(model, *, active=False):
            if children is not None:
                return children[model]
            return self.canvas_dao.children(
                model, canvas.id, active=active, lock=lock, refresh=refresh
            )

        nodes = sorted(rows(CanvasNode, active=True), key=lambda x: x.z_index)
        edges = sorted(rows(CanvasEdge, active=True), key=lambda x: x.position)
        document = {
            **deepcopy(canvas.properties_json),
            "id": canvas.source_key,
            "workspaceProjectId": str(canvas.project_id),
            "title": canvas.title,
            "revision": str(canvas.row_version),
            "createdAt": iso(canvas.created_at),
            "updatedAt": iso(canvas.updated_at),
            "nodes": [
                {
                    **deepcopy(node.content_json),
                    "id": node.node_key,
                    "type": node.kind,
                    "position": {"x": node.x, "y": node.y},
                    "width": node.width,
                    "height": node.height,
                    **({"parentId": node.parent_node_key} if node.parent_node_key else {}),
                }
                for node in nodes
            ],
            "connections": [
                {
                    **deepcopy(edge.context_json),
                    "id": edge.edge_key,
                    "fromNodeId": edge.from_node_key,
                    "toNodeId": edge.to_node_key,
                    **({"fromHandleId": edge.from_port} if edge.from_port is not None else {}),
                    **({"toHandleId": edge.to_port} if edge.to_port is not None else {}),
                    **({"relation": edge.relation} if edge.relation is not None else {}),
                }
                for edge in edges
            ],
            "directorScenes": [
                deepcopy(scene.scene_json)
                for scene in sorted(
                    rows(CanvasDirectorScene),
                    key=lambda x: x.position,
                )
            ],
        }
        timelines = rows(CanvasTimeline)
        if timelines:
            document["timeline"] = deepcopy(timelines[0].document_json)
        # Pre-upgrade documents may contain an unverified private classification.
        # Only the owning account's canonical membership may expose this field.
        document.pop("folderId", None)
        return document

    def _private_state(self, canvas, *, children=None, lock=False, refresh=False):
        def rows(model):
            return (
                children[model]
                if children is not None and model in children
                else self.canvas_dao.children(model, canvas.id, lock=lock, refresh=refresh)
            )

        states = rows(CanvasUserState)
        state = states[0] if states else None
        root = deepcopy(state.preferences_json) if state else {}
        root.pop("folderId", None)
        root.pop("projectId", None)
        folders = rows(CanvasProjectFolderItem)
        if folders:
            root["folderId"] = folders[0].folder_key
        root["viewport"] = deepcopy(state.viewport_json) if state else {"x": 0, "y": 0, "k": 1}
        return {
            "root": root,
            "nodes": {
                value.node_key: deepcopy(value.draft_json) for value in rows(CanvasNodeUserState)
            },
        }

    def _document(self, canvas, *, private, children=None, lock=False, refresh=False):
        document = project_document(
            self._shared_document(canvas, children=children, lock=lock, refresh=refresh),
            self._private_state(canvas, children=children, lock=lock, refresh=refresh)
            if private
            else None,
        )
        if private:
            document.setdefault("chatSessions", [])
            document.setdefault("activeChatId", None)
            document.setdefault("backgroundMode", "dots")
            document.setdefault("showImageInfo", False)
        return canonicalize_canvas_media(document, strict=False)

    def read(self, project_id, canvas_id, *, private=False):
        with self._transaction(read_only=True):
            canvas = self.require_canvas(project_id, canvas_id, refresh=True)
            document = self._document(canvas, private=private, refresh=True)
            return {
                **self.summary(canvas),
                "source_document": document,
                "resource_aliases": self._resource_aliases(document) if private else {},
            }

    def _resource_aliases(self, *documents: dict) -> dict[str, list[str]]:
        identifiers = {value for document in documents for _, value in media_references(document)}
        ancestors = CanvasResourceDAO(self.session).copy_ancestors(identifiers)
        return {
            str(key): [str(value) for value in sorted(values)] for key, values in ancestors.items()
        }

    def list_for_project(self, project_id):
        with self._transaction(read_only=True):
            self.require_project(project_id)
            return {"items": [self.summary(x) for x in self.canvas_dao.canvases(project_id)]}

    def resolve(self, source_key):
        with self._transaction(read_only=True):
            query = select(ProjectCanvas).where(ProjectCanvas.archived_at.is_(None))
            if source_key.isdecimal():
                from sqlalchemy import or_

                query = query.where(
                    or_(ProjectCanvas.id == int(source_key), ProjectCanvas.source_key == source_key)
                )
            else:
                query = query.where(ProjectCanvas.source_key == source_key)
            canvas = self.session.scalar(query)
            if canvas is None:
                raise NotFound("Canvas does not exist")
            self.require_project(canvas.project_id)
            return self.summary(canvas)

    def list_workspace(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        query: str = "",
        project_id: int | None = None,
        sort: str = "updated",
        include_documents: bool = False,
    ) -> dict:
        with self._transaction(read_only=True):
            canvases, total = self.canvas_dao.workspace_page(
                page=page,
                page_size=page_size,
                query=query.strip(),
                project_id=project_id,
                sort=sort,
            )
            children = self.canvas_dao.workspace_children(
                canvases,
                (
                    CanvasNode,
                    CanvasEdge,
                    CanvasDirectorScene,
                    CanvasTimeline,
                    CanvasUserState,
                    CanvasNodeUserState,
                    CanvasProjectFolderItem,
                ),
            )
            items = []
            for canvas in canvases:
                document = self._document(canvas, private=True, children=children[canvas.id])
                items.append(
                    {
                        **self.summary(canvas),
                        "folder_id": document.get("folderId"),
                        "canvas_title": document.get("canvasTitle"),
                        "node_count": len(document["nodes"]),
                        "preview_nodes": [] if include_documents else document["nodes"],
                        **({"source_document": document} if include_documents else {}),
                    }
                )
            if include_documents:
                aliases = self._resource_aliases(*(item["source_document"] for item in items))
                for item in items:
                    own_ids = {str(value) for _, value in media_references(item["source_document"])}
                    item["resource_aliases"] = {
                        key: value for key, value in aliases.items() if key in own_ids
                    }
            return {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_more": page * page_size < total,
            }
