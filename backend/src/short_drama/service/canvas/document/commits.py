from contextlib import nullcontext
from copy import deepcopy

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas.resources.resource import CanvasResourceDAO
from short_drama.domain.canvas.document.canvas import (
    CanvasDirectorScene,
    CanvasEdge,
    CanvasMediaReference,
    CanvasNode,
    CanvasNodeUserState,
    CanvasTimeline,
    CanvasUserMediaReference,
    CanvasUserState,
)
from short_drama.domain.canvas.resources.resource import (
    CanvasBinaryReference,
    CanvasBinaryResource,
    CanvasUserBinaryReference,
)
from short_drama.schemas.canvas.document.canvas import (
    CanvasCommitRequest,
    CanvasDocument,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.audit import iso as iso
from short_drama.service.canvas.document.base import CanvasServiceBase
from short_drama.service.canvas.document.document import (
    SKILL_BODY_METADATA,
    content_hash,
    media_references,
    project_document,
    split_document,
)
from short_drama.service.canvas.resources.media_locators import canonicalize_canvas_media
from short_drama.service.canvas.workspace.folders.folder_state import (
    assign_canvas_folder,
    canvas_folder_write_lock,
)
from short_drama.service.creation.writing.script.publication import publish


class CanvasCommits(CanvasServiceBase):
    def _validate_identity(self, canvas, document):
        if document.id != canvas.source_key or document.workspace_project_id not in {
            None,
            str(canvas.project_id),
        }:
            raise WorkflowError(
                "canvas_identity_mismatch", "Document belongs to another canvas", 422
            )

    @staticmethod
    def _check_version(canvas, expected):
        if canvas.row_version != expected:
            raise WorkflowError(
                "canvas_revision_conflict",
                "Canvas changed; keep the draft and merge explicitly",
                409,
                {"current_version": str(canvas.row_version)},
            )

    @staticmethod
    def _check_legacy_skill_body(document):
        if any(
            node.get("type") == "skill"
            and SKILL_BODY_METADATA.intersection(node.get("metadata", {}))
            for node in document.get("nodes", [])
        ):
            raise WorkflowError(
                "canvas_legacy_skill_body_private",
                "旧版 Skill 正文尚未确认归属，已保留原始内容；请先由管理员确认作者并迁移至本人状态",
                409,
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
            self._transaction(lock_request_project=False),
        ):
            canvas = self.require_canvas(
                project_id,
                canvas_id,
                lock=True,
            )
            self._validate_identity(canvas, request.source_document)
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
        from short_drama.service.canvas.library.library_references import canvas_media_bindings

        raw = document.model_dump(mode="json", by_alias=True, exclude_unset=True)
        identifiers = {value for key, value in canvas_media_bindings(raw) if key}
        if identifiers:
            with self._transaction(read_only=True):
                if CanvasResourceDAO(self.session).copy_ancestors(identifiers):
                    return self._global_order_lock()
        return nullcontext()

    def _bind_library_copies(self, document):
        from short_drama.service.canvas.library.library_copy_bindings import (
            bind_canvas_library_copies,
        )

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
        self._check_legacy_skill_body(before)
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
            if edge.edge_key not in wanted_edges and edge.archived_at is None:
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
            elif any(getattr(edge, key) != value for key, value in values.items()):
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
        resources = CanvasResourceDAO(self.session).resource_map(
            {item[1] for item in references}, lock=True
        )
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
        previous = self.canvas_dao.current_resource_references(model, canvas.id)
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
                if getattr(reference, field) != identifier:
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
