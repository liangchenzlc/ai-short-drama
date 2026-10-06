"""Atomic canvas commits; private drafts never become shared document fields."""

import json
import re
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, timedelta
from uuid import uuid4

from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_dao import CanvasDAO
from short_drama.dao.canvas_drawing_dao import CanvasDrawingDAO
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.domain import Project, ProjectCanvas, ProjectCanvasSettings
from short_drama.domain.canvas import (
    CanvasDirectorScene,
    CanvasEdge,
    CanvasMediaReference,
    CanvasNode,
    CanvasNodeUserState,
    CanvasRevision,
    CanvasRevisionMediaReference,
    CanvasRevisionUserState,
    CanvasTimeline,
    CanvasUserMediaReference,
    CanvasUserState,
    CanvasWriteReceipt,
)
from short_drama.domain.canvas_folder import CanvasProjectFolderItem
from short_drama.domain.canvas_resource import (
    CanvasBinaryReference,
    CanvasBinaryResource,
    CanvasUserBinaryReference,
)
from short_drama.schemas.canvas import (
    CanvasCommitRequest,
    CanvasCreateRequest,
    CanvasDocument,
    CanvasUserStateRequest,
    CanvasViewportRequest,
    CanvasViewPreferences,
    CanvasViewPreferencesRequest,
)
from short_drama.schemas.canvas_creation import canvas_creation_payload
from short_drama.utils.snowflake import next_id

from .base import BaseService, utcnow
from .canvas_document import content_hash, media_references, project_document, split_document
from .canvas_drawing_history import (
    bind_revision_drawings,
    check_drawing_heads,
    freeze_drawings,
    restore_revision_drawings,
)
from .canvas_folder_state import assign_canvas_folder, canvas_folder_write_lock
from .canvas_media_locators import canonicalize_canvas_media
from .publication import publish


def iso(value):
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def validate_key(key: str | None) -> str:
    if not key or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
        raise WorkflowError(
            "canvas_idempotency_required", "A stable Idempotency-Key is required", 422
        )
    return key


class CanvasService(BaseService):
    model = ProjectCanvas

    def __init__(self, session):
        super().__init__(session)
        self.canvas_dao = CanvasDAO(session)

    @property
    def actor_id(self) -> int:
        actor = self.session.info.get("actor")
        if actor is None:
            raise WorkflowError("authentication_required", "Sign in to use canvases", 401)
        return actor.user_id

    def audit(self) -> dict:
        now = utcnow()
        return dict(
            id=next_id(),
            created_at=now,
            updated_at=now,
            created_by=self.actor_id,
            updated_by=self.actor_id,
        )

    def child(self, canvas) -> dict:
        return {**self.audit(), "project_id": canvas.project_id, "canvas_id": canvas.id}

    def require_project(self, project_id: int, *, lock=False):
        project = self.canvas_dao.project(project_id, lock=lock)
        if project is None:
            raise NotFound("Project does not exist")
        if project.workspace_mode != "infinite_canvas":
            raise WorkflowError("canvas_mode_required", "This project uses standard mode", 409)
        return project

    def require_canvas(self, project_id: int, canvas_id: int, *, lock=False):
        self.require_project(project_id, lock=lock)
        canvas = self.canvas_dao.canvas(project_id, canvas_id, lock=lock)
        if canvas is None:
            raise NotFound("Canvas does not exist")
        return canvas

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

    def _shared_document(self, canvas, *, children=None, lock=False):
        def rows(model, *, active=False):
            if children is not None:
                return children[model]
            return self.canvas_dao.children(model, canvas.id, active=active, lock=lock)

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

    def _private_state(self, canvas, *, children=None, lock=False):
        def rows(model):
            return (
                children[model]
                if children is not None and model in children
                else self.canvas_dao.children(model, canvas.id, lock=lock)
            )

        states = rows(CanvasUserState)
        state = states[0] if states else None
        root = deepcopy(state.preferences_json) if state else {}
        root.pop("folderId", None)
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

    def _document(self, canvas, *, private, children=None, lock=False):
        document = self._shared_document(canvas, children=children, lock=lock)
        if private:
            document = project_document(
                document, self._private_state(canvas, children=children, lock=lock)
            )
            document.setdefault("chatSessions", [])
            document.setdefault("activeChatId", None)
            document.setdefault("backgroundMode", "dots")
            document.setdefault("showImageInfo", False)
        return canonicalize_canvas_media(document, strict=False)

    def read(self, project_id, canvas_id, *, private=False):
        with self._transaction(read_only=True):
            canvas = self.require_canvas(project_id, canvas_id)
            document = self._document(canvas, private=private)
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

    def create_for_project(self, project_id, payload, key):
        request = CanvasCreateRequest.model_validate(
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, CanvasCreateRequest)
            else payload
        )
        with (
            self._copy_binding_lock(request.source_document),
            canvas_folder_write_lock(self.session),
            self._transaction(),
        ):
            return self.create_in_transaction(request, key, project_id=project_id)

    def create_workspace(self, payload: CanvasCreateRequest, key: str) -> dict:
        """Source new-project / duplicate actions atomically create their host owner."""
        with (
            self._copy_binding_lock(payload.source_document),
            canvas_folder_write_lock(self.session),
            self._transaction(),
        ):
            return self.create_in_transaction(payload, key)

    def create_in_transaction(
        self, request, key, *, project_id=None, source_key=None, prepare_document=None
    ):
        """Creation coordinator owns the transaction and any prepared file attachment."""
        operation = "canvas.create" if project_id is not None else "canvas.workspace.create"
        if project_id is not None:
            self.require_project(project_id, lock=True)
        original = canvas_creation_payload(request)
        if project_id is not None:
            original = {"project_id": str(project_id), **original}
        digest, receipt = self.begin_write(key, operation, original)
        if receipt:
            return receipt.result_json
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
                aspect="16:9",
                synopsis="",
                style="",
                workspace_mode="infinite_canvas",
                last_opened_at=utcnow(),
                row_version=1,
            )
            self.session.add(project)
            self.session.flush()
            canvas = self.initialize_project(
                project, source_key=source_key or request.source_key, title=request.title
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

    def _validate_identity(self, canvas, document):
        if document.id != canvas.source_key or document.workspace_project_id not in {
            None,
            str(canvas.project_id),
        }:
            raise WorkflowError(
                "canvas_identity_mismatch", "Document belongs to another canvas", 422
            )
        if document.project_id:
            # BeefTV projectId is a separate short-drama binding, never the owning workspace ID.
            raise WorkflowError("canvas_binding_invalid", "Unverified business binding", 422)

    @staticmethod
    def _check_version(canvas, expected):
        if canvas.row_version != expected:
            raise WorkflowError(
                "canvas_revision_conflict",
                "Canvas changed; keep the draft and merge explicitly",
                409,
                {"current_version": str(canvas.row_version)},
            )

    def commit(self, project_id, canvas_id, payload, key):
        request = CanvasCommitRequest.model_validate(
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, CanvasCommitRequest)
            else payload
        )
        with (
            self._copy_binding_lock(request.source_document),
            canvas_folder_write_lock(self.session),
            self._transaction(),
        ):
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
            digest, receipt = self.begin_write(
                key,
                "canvas.commit",
                {
                    "project_id": str(project_id),
                    "canvas_id": str(canvas_id),
                    **request.model_dump(mode="json"),
                },
            )
            if receipt:
                return receipt.result_json
            self._check_version(canvas, request.expected_row_version)
            self._validate_identity(canvas, request.source_document)
            self._bind_library_copies(request.source_document)
            self._apply_document(canvas, request.source_document)
            result = self.summary(canvas)
            self.record_write(
                key=key,
                operation="canvas.commit",
                digest=digest,
                canvas=canvas,
                result=result,
                expected=request.expected_row_version,
            )
            return result

    def _copy_binding_lock(self, document):
        if document is None:
            return nullcontext()
        from .canvas_library_references import canvas_media_bindings

        raw = document.model_dump(mode="json", by_alias=True, exclude_unset=True)
        identifiers = {value for key, value in canvas_media_bindings(raw) if key}
        if identifiers:
            with self._transaction(read_only=True):
                if CanvasResourceDAO(self.session).copy_ancestors(identifiers):
                    return self._global_order_lock()
        return nullcontext()

    def _bind_library_copies(self, document):
        from .canvas_library_copy_bindings import bind_canvas_library_copies

        bind_canvas_library_copies(
            self.session, document.model_dump(mode="json", by_alias=True, exclude_unset=True)
        )

    def _apply_document(self, canvas, document, *, reason="automatic"):
        shared, private = split_document(document)
        # Viewport saves are independent of graph commits. An older graph request must not
        # rewind a position already acknowledged by the personal viewport endpoint.
        states = self.canvas_dao.children(CanvasUserState, canvas.id, lock=True)
        private["root"]["viewport"] = (
            deepcopy(states[0].viewport_json) if states else {"x": 0, "y": 0, "k": 1}
        )
        saved_preferences = states[0].preferences_json if states else {}
        for field in ("appearance", "backgroundMode", "showImageInfo"):
            private["root"].pop(field, None)
            if field in saved_preferences:
                private["root"][field] = deepcopy(saved_preferences[field])
        before = self._shared_document(canvas, lock=True)
        # Do not replace pre-upgrade positional drafts without first proving that
        # they can be projected. Current deployments have no legacy private rows.
        previous_private = self._private_state(canvas, lock=True)
        project_document(before, previous_private)
        folder_changed = (previous_private["root"].get("folderId") or "") != (
            private["root"].get("folderId") or ""
        )
        old_shared, _ = split_document(CanvasDocument.model_validate(before), strict_media=False)
        if (
            shared != old_shared
            or folder_changed
            or before != canonicalize_canvas_media(before, strict=False)
            or reason == "before_restore"
        ):
            self._snapshot(canvas, before, reason=reason)
            self._replace_graph(canvas, shared)
            canvas.row_version += 1
            canvas.updated_at = utcnow()
        self._save_private(canvas, private)
        self.session.flush()

    def _replace_graph(self, canvas, document):
        now = utcnow()
        nodes = {x.node_key: x for x in self.canvas_dao.children(CanvasNode, canvas.id, lock=True)}
        wanted = {x["id"] for x in document.get("nodes", [])}
        for node in nodes.values():
            if node.node_key not in wanted and node.archived_at is None:
                node.archived_at, node.updated_at = now, now
                node.row_version += 1
        parents = []
        for index, data in enumerate(document.get("nodes", [])):
            content = {
                k: deepcopy(v)
                for k, v in data.items()
                if k not in {"id", "type", "position", "width", "height", "parentId"}
            }
            values = dict(
                kind=data["type"],
                x=data["position"]["x"],
                y=data["position"]["y"],
                width=data["width"],
                height=data["height"],
                z_index=index,
                content_json=content,
                archived_at=None,
            )
            node = nodes.get(data["id"])
            if node is None:
                node = CanvasNode(
                    **self.child(canvas),
                    node_key=data["id"],
                    parent_node_key=None,
                    row_version=1,
                    content_version=1,
                    **values,
                )
                self.session.add(node)
            elif any(
                getattr(node, k) != v for k, v in values.items()
            ) or node.parent_node_key != data.get("parentId"):
                node.row_version += 1
                if node.content_json != content or node.kind != data["type"]:
                    node.content_version += 1
                for key, value in values.items():
                    setattr(node, key, value)
                node.updated_at = now
            parents.append((node, data.get("parentId")))
        # MySQL checks self references per row, so all new node keys precede parent links.
        self.session.flush()
        for node, parent in parents:
            node.parent_node_key = parent
        edges = {x.edge_key: x for x in self.canvas_dao.children(CanvasEdge, canvas.id, lock=True)}
        wanted_edges = {x["id"] for x in document.get("connections", [])}
        for edge in edges.values():
            if edge.edge_key not in wanted_edges:
                edge.archived_at, edge.updated_at = now, now
        for index, data in enumerate(document.get("connections", [])):
            values = dict(
                from_node_key=data["fromNodeId"],
                to_node_key=data["toNodeId"],
                from_port=data.get("fromHandleId"),
                to_port=data.get("toHandleId"),
                relation=data.get("relation"),
                position=index,
                archived_at=None,
                context_json={
                    k: deepcopy(v)
                    for k, v in data.items()
                    if k
                    not in {
                        "id",
                        "fromNodeId",
                        "toNodeId",
                        "fromHandleId",
                        "toHandleId",
                        "relation",
                    }
                },
            )
            edge = edges.get(data["id"])
            if edge is None:
                self.session.add(CanvasEdge(**self.child(canvas), edge_key=data["id"], **values))
            else:
                for key, value in values.items():
                    setattr(edge, key, value)
                edge.updated_at = now
        canvas.title = document["title"]
        canvas.properties_json = {
            k: deepcopy(v)
            for k, v in document.items()
            if k not in {"title", "nodes", "connections", "timeline", "directorScenes"}
        }
        self._replace_subdocuments(canvas, document)
        self._replace_media_refs(canvas, document, private=False)
        self.session.flush()

    def _replace_subdocuments(self, canvas, document):
        timelines = self.canvas_dao.children(CanvasTimeline, canvas.id, lock=True)
        timeline = timelines[0] if timelines else None
        if document.get("timeline") is None:
            if timeline:
                self.session.delete(timeline)
        elif timeline:
            if timeline.document_json != document["timeline"]:
                timeline.document_json = deepcopy(document["timeline"])
                timeline.row_version += 1
                timeline.updated_at = utcnow()
        else:
            self.session.add(
                CanvasTimeline(
                    **self.child(canvas),
                    document_json=deepcopy(document["timeline"]),
                    schema_version=1,
                    row_version=1,
                )
            )
        scenes = {
            x.scene_key: x
            for x in self.canvas_dao.children(CanvasDirectorScene, canvas.id, lock=True)
        }
        seen = set()
        for position, scene in enumerate(document.get("directorScenes", [])):
            key = scene.get("id")
            if not isinstance(key, str) or not key or len(key) > 128 or key in seen:
                raise WorkflowError(
                    "canvas_scene_invalid", "Director scenes require unique stable IDs", 422
                )
            seen.add(key)
            stored = scenes.get(key)
            if stored:
                if stored.scene_json != scene or stored.position != position:
                    stored.scene_json, stored.position = deepcopy(scene), position
                    stored.row_version += 1
                    stored.updated_at = utcnow()
            else:
                self.session.add(
                    CanvasDirectorScene(
                        **self.child(canvas),
                        scene_key=key,
                        position=position,
                        scene_json=deepcopy(scene),
                        schema_version=1,
                        row_version=1,
                    )
                )
        for key, scene in scenes.items():
            if key not in seen:
                self.session.delete(scene)

    def _save_private(self, canvas, private):
        states = self.canvas_dao.children(CanvasUserState, canvas.id, lock=True)
        root = deepcopy(private.get("root", {}))
        assign_canvas_folder(self.session, canvas, root.pop("folderId", None))
        viewport = root.pop("viewport", {"x": 0, "y": 0, "k": 1})
        if states:
            state = states[0]
            if state.viewport_json != viewport or state.preferences_json != root:
                state.viewport_json, state.preferences_json = viewport, root
                state.row_version += 1
                state.updated_at = utcnow()
        else:
            self.session.add(
                CanvasUserState(
                    **self.child(canvas),
                    user_id=self.actor_id,
                    viewport_json=viewport,
                    preferences_json=root,
                    row_version=1,
                )
            )
        nodes = {
            x.node_key: x
            for x in self.canvas_dao.children(CanvasNodeUserState, canvas.id, lock=True)
        }
        for key, data in private.get("nodes", {}).items():
            if key in nodes:
                state = nodes.pop(key)
                if state.draft_json != data:
                    state.draft_json, state.updated_at = deepcopy(data), utcnow()
                    state.row_version += 1
            elif data:
                self.session.add(
                    CanvasNodeUserState(
                        **self.child(canvas),
                        user_id=self.actor_id,
                        node_key=key,
                        draft_json=deepcopy(data),
                        row_version=1,
                    )
                )
        for state in nodes.values():
            self.session.delete(state)
        self._replace_media_refs(canvas, private, private=True)

    def _replace_media_refs(self, canvas, document, *, private):
        references = list(media_references(document))
        resources = CanvasResourceDAO(self.session).resource_map({item[1] for item in references})
        for binary in (False, True):
            self._replace_resource_refs(
                canvas, document, references, resources, private=private, binary=binary
            )

    def _replace_resource_refs(self, canvas, document, references, resources, *, private, binary):
        if binary:
            model = CanvasUserBinaryReference if private else CanvasBinaryReference
            field = "binary_id"
        else:
            model = CanvasUserMediaReference if private else CanvasMediaReference
            field = "media_id"
        previous = [
            x
            for x in self.canvas_dao.children(model, canvas.id, lock=True)
            if x.owner_kind != "revision"
        ]
        desired = {}
        for path, identifier in references:
            media = resources.get(identifier)
            if media is None:
                raise NotFound("Canvas resource does not exist")
            if isinstance(media, CanvasBinaryResource) != binary:
                continue
            if not private and len(path) > 2 and path[0] == "nodes":
                node_key = document["nodes"][int(path[1])]["id"]
                kind, owner = "node", node_key
            else:
                node_key = None
                kind, owner = (
                    ("private", str(self.actor_id)) if private else ("document", canvas.source_key)
                )
            slot = content_hash(path)
            desired[(kind, owner, slot, 0)] = (identifier, node_key, media)
        existing = {(x.owner_kind, x.owner_key, x.slot, x.ordinal): x for x in previous}
        for key, (identifier, node_key, media) in desired.items():
            valid = media is not None and (
                media.project_id == canvas.project_id
                and (private or media.published_at is not None or media.created_by == self.actor_id)
                or private
                and media.scope_user_id == self.actor_id
            )
            if not valid:
                raise NotFound("Canvas media is missing or outside the permitted scope")
            if not private:
                # Attaching output is the original canvas publication action; prompt/reference
                # fields live in the private projection and never reach this branch.
                publish(media)
            reference = existing.pop(key, None)
            if reference:
                setattr(reference, field, identifier)
                reference.updated_at = utcnow()
            else:
                values = {"user_id": self.actor_id} if private else {"node_key": node_key}
                self.session.add(
                    model(
                        **self.child(canvas),
                        owner_kind=key[0],
                        owner_key=key[1],
                        slot=key[2],
                        ordinal=0,
                        **{field: identifier},
                        **values,
                    )
                )
        for reference in existing.values():
            self.session.delete(reference)

    def _snapshot(self, canvas, before, *, reason):
        before = canonicalize_canvas_media(before, strict=False)
        revisions = sorted(
            self.canvas_dao.children(CanvasRevision, canvas.id, lock=True),
            key=lambda x: x.canvas_row_version,
            reverse=True,
        )
        if revisions and (
            revisions[0].canvas_row_version == canvas.row_version
            or (
                reason != "before_restore"
                and revisions[0].created_at > utcnow() - timedelta(minutes=5)
            )
        ):
            return
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
        for path, identifier in media_references(private):
            resource = CanvasResourceDAO(self.session).resource(identifier)
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
        for identifier in sorted({value for _, value in media_references(before)}):
            resource = CanvasResourceDAO(self.session).resource(identifier)
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

    def _revision_document(self, revision):
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
            self.require_canvas(project_id, canvas_id)
            revision = self._revision(canvas_id, revision_id)
            document = self._revision_document(revision)
            return {
                "revision": self._revision_summary(revision),
                "source_document": document,
                "resource_aliases": self._resource_aliases(document),
            }

    def restore(
        self, project_id, canvas_id, revision_id, expected, key, expected_drawing_heads=None
    ):
        with canvas_folder_write_lock(self.session), self._transaction():
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
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
            document = self._revision_document(revision)
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

    def read_user_state(self, project_id, canvas_id):
        with self._transaction(read_only=True):
            canvas = self.require_canvas(project_id, canvas_id)
            states = self.canvas_dao.children(CanvasUserState, canvas.id)
            state = states[0] if states else None
            return {
                "row_version": str(state.row_version) if state else "0",
                "viewport": state.viewport_json if state else {"x": 0, "y": 0, "k": 1},
                "preferences": state.preferences_json if state else {},
            }

    def update_user_state(self, project_id, canvas_id, payload):
        request = CanvasUserStateRequest.model_validate(
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, CanvasUserStateRequest)
            else payload
        )
        with self._transaction():
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
            states = self.canvas_dao.children(CanvasUserState, canvas_id, lock=True)
            state = states[0] if states else None
            if request.expected_row_version != (state.row_version if state else 0):
                raise WorkflowError(
                    "canvas_user_state_conflict", "Personal canvas preferences changed", 409
                )
            private = self._private_state(canvas, lock=True)
            if request.viewport is not None:
                private["root"]["viewport"] = request.viewport.model_dump()
            if request.preferences is not None:
                private["root"].update(request.preferences)
            self._save_private(canvas, private)
            self.session.flush()
            state = self.canvas_dao.children(CanvasUserState, canvas_id, lock=True)[0]
            return {
                "row_version": str(state.row_version),
                "viewport": state.viewport_json,
                "preferences": state.preferences_json,
            }

    def update_viewport(
        self, project_id: int, canvas_id: int, payload: CanvasViewportRequest
    ) -> dict:
        with self._transaction():
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
            states = self.canvas_dao.children(CanvasUserState, canvas_id, lock=True)
            state = states[0] if states else None
            current = state.viewport_json if state else {"x": 0, "y": 0, "k": 1}
            desired = payload.viewport.model_dump()
            # Equality recovers a lost acknowledgement without another write. CAS only the
            # viewport field so unrelated personal preferences do not create false conflicts.
            if current != desired:
                if current != payload.expected_viewport.model_dump():
                    raise WorkflowError(
                        "canvas_viewport_conflict",
                        "视口已在其他窗口更新，本机位置已保留；请重新加载后继续保存",
                        409,
                    )
                if state is None:
                    state = CanvasUserState(
                        **self.child(canvas),
                        user_id=self.actor_id,
                        viewport_json=desired,
                        preferences_json={},
                        row_version=1,
                    )
                    self.session.add(state)
                else:
                    state.viewport_json = desired
                    state.row_version += 1
                    state.updated_at = utcnow()
                    state.updated_by = self.actor_id
                self.session.flush()
            return {
                "row_version": str(state.row_version) if state else "0",
                "viewport": desired,
                "preferences": state.preferences_json if state else {},
            }

    def update_view_preferences(
        self, project_id: int, canvas_id: int, payload: CanvasViewPreferencesRequest
    ) -> dict:
        with self._transaction():
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
            states = self.canvas_dao.children(CanvasUserState, canvas.id, lock=True)
            state = states[0] if states else None
            previous = state.preferences_json if state else {}
            keys = {"appearance", "backgroundMode", "showImageInfo"}
            current = CanvasViewPreferences.model_validate(
                {key: value for key, value in previous.items() if key in keys}
            )
            desired = payload.preferences
            if current != desired:
                if current != payload.expected_preferences:
                    raise WorkflowError(
                        "canvas_view_preferences_conflict",
                        "外观已在其他窗口更新，本机设置已保留；请重新加载后继续保存",
                        409,
                    )
                preferences = {
                    key: deepcopy(value) for key, value in previous.items() if key not in keys
                }
                preferences.update(
                    desired.model_dump(mode="json", by_alias=True, exclude_none=True)
                )
                if state is None:
                    state = CanvasUserState(
                        **self.child(canvas),
                        user_id=self.actor_id,
                        viewport_json={"x": 0, "y": 0, "k": 1},
                        preferences_json=preferences,
                        row_version=1,
                    )
                    self.session.add(state)
                else:
                    state.preferences_json = preferences
                    state.row_version += 1
                    state.updated_at = utcnow()
                    state.updated_by = self.actor_id
                self.session.flush()
            return {
                "row_version": str(state.row_version) if state else "0",
                "preferences": desired,
            }

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
