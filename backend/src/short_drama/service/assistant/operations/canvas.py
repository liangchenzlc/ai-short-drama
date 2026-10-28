"""Atomic, scoped increments over the native canvas document and private author state."""

import math
import re
from collections import Counter, defaultdict, deque
from copy import deepcopy
from typing import Any

from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import AIModelConfig, Project, ProjectCanvas
from short_drama.domain.canvas.document.canvas import CanvasNode, CanvasNodeUserState
from short_drama.schemas.assistant.operations.canvas import AssistantCanvasPatch
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.schemas.canvas.document.canvas import CanvasDocument
from short_drama.service.base import BaseService
from short_drama.service.canvas.document.canvas import CanvasService
from short_drama.service.canvas.document.document import split_document

# Version 1 matches the four native NODE_SPECS; no source interaction or styling is replaced.
CANVAS_NODE_SPECS = {
    "text": {"title": "文本节点", "width": 350, "height": 350},
    "image": {"title": "图片", "width": 720, "height": 405},
    "video": {"title": "视频", "width": 720, "height": 405},
    "audio": {"title": "音频", "width": 320, "height": 320},
}
_SETTINGS = {
    "text": set(),
    "image": {"size", "quality"},
    "video": {"size", "seconds", "vquality"},
    "audio": {"audioVoice", "audioFormat", "audioSpeed", "audioInstructions"},
}


def node_input_kind(node: dict) -> str:
    if node.get("metadata", {}).get("workflowKind") == "character":
        return "character"
    return node["type"]


def check_canvas_inputs(target: dict, sources: list[dict], profile: dict | None) -> None:
    """Count distinct direct inputs against this selected model, never a directory maximum."""
    counts = Counter(node_input_kind(node) for node in {n["id"]: n for n in sources}.values())
    kind = target["type"]
    if kind not in CANVAS_NODE_SPECS:
        raise WorkflowError("assistant_canvas_node_type", "助手尚未开放此目标节点类型", 422)
    if any(source not in {"text", "image", "video", "audio", "character"} for source in counts):
        raise WorkflowError("assistant_canvas_input_type", "助手尚未开放此来源节点类型", 422)
    if kind == "image" and (counts["video"] or counts["audio"]):
        raise WorkflowError("assistant_canvas_input_type", "图片节点不能连接参考视频或音频", 422)
    if kind == "text" and counts["audio"]:
        raise WorkflowError("assistant_canvas_input_type", "文本节点不能连接参考音频", 422)
    if kind == "audio":
        if counts["character"] > 1 or any(counts[value] for value in ("image", "video", "audio")):
            raise WorkflowError(
                "assistant_canvas_input_type", "音频节点只接受文本或单个角色卡输入", 422
            )
        return
    media = {
        "image": counts["image"] + counts["character"],
        "video": counts["video"],
        "audio": counts["audio"],
    }
    if not any(media.values()):
        return
    if profile is None:
        raise WorkflowError(
            "assistant_canvas_model_required", "连接媒体参考前请明确目标节点实际使用的模型", 422
        )
    logical = profile.get("logical_capability_profiles") or [profile.get("logical_capability_spec")]
    specs = [item for item in logical if isinstance(item, dict) and item.get("capability") == kind]

    def maximum(value):
        return value if type(value) is int and 0 <= value <= 100 else 0

    def input_limit(spec, source):
        inputs = spec.get("inputs")
        bounds = inputs.get(source) if isinstance(inputs, dict) else None
        return maximum(bounds.get("max")) if isinstance(bounds, dict) else 0

    if specs:
        if any(
            all(count <= input_limit(spec, source) for source, count in media.items())
            for spec in specs
        ):
            return
    else:
        references = ((profile.get("capability_config") or {}).get(kind) or {}).get(
            "references"
        ) or {}
        references = references if isinstance(references, dict) else {}
        limits = {"image": "maxImages", "video": "maxVideos", "audio": "maxAudios"}
        if all(count <= maximum(references.get(limits[source])) for source, count in media.items()):
            return
    raise WorkflowError(
        "assistant_canvas_input_capacity", "连线超过目标所选模型的参考类型或数量上限", 422
    )


def layout_new_nodes(nodes: dict[str, dict], edges: list[dict], new_ids: list[str]) -> None:
    """Stable left-to-right layers to the right of occupied world-space rectangles."""
    new = set(new_ids)
    if not new:
        return
    incoming, outgoing = Counter(), defaultdict(list)
    for edge in edges:
        source, target = edge["fromNodeId"], edge["toNodeId"]
        if source in new and target in new:
            incoming[target] += 1
            outgoing[source].append(target)
    levels = {identifier: 0 for identifier in new_ids}
    ready = deque(identifier for identifier in new_ids if not incoming[identifier])
    visited = 0
    while ready:
        source = ready.popleft()
        visited += 1
        for target in outgoing[source]:
            levels[target] = max(levels[target], levels[source] + 1)
            incoming[target] -= 1
            if not incoming[target]:
                ready.append(target)
    if visited != len(new):
        raise WorkflowError("assistant_canvas_cycle", "新增生成依赖存在循环", 422)
    right = 0.0
    for identifier, node in nodes.items():
        if identifier in new:
            continue
        x, parent, seen = node["position"]["x"], node.get("parentId"), set()
        while parent:
            if parent in seen or parent not in nodes:
                raise WorkflowError("assistant_canvas_cycle", "已有父子结构无效，请先修复画布", 409)
            seen.add(parent)
            x += nodes[parent]["position"]["x"]
            parent = nodes[parent].get("parentId")
        right = max(right, x + node["width"] + 120)
    for level in range(max(levels.values()) + 1):
        column = [nodes[identifier] for identifier in new_ids if levels[identifier] == level]
        y = 0
        for node in column:
            node["position"] = {"x": right, "y": y}
            y += node["height"] + 80
        right += max(node["width"] for node in column) + 120
    if not math.isfinite(right):
        raise Conflict("画布坐标已超出可布局范围，请先调整占位区域")


class AssistantCanvasService(BaseService):
    model = ProjectCanvas

    def apply_locked(
        self,
        project_id: int | str,
        source_key: str,
        payload: AssistantCanvasPatch | dict,
        *,
        owner_user_id: int | str,
        operation_id: int | str,
        allowed_node_versions: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        """Caller owns trusted scope, operation replay and a savepoint for the complete batch."""
        if not self.session.in_transaction():
            raise BusinessError("Canvas increment requires the caller's transaction")
        batch = AssistantCanvasPatch.model_validate(self._payload(AssistantCanvasPatch, payload))
        owner = parse_identifier(owner_user_id)
        project_id, operation_id = parse_identifier(project_id), parse_identifier(operation_id)
        project = self.session.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.owner_user_id == owner,
                Project.workspace_mode == "infinite_canvas",
                Project.archived_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if project is None:
            raise NotFound("画布写入目标不存在或无权访问")
        original_actor = self.session.info.get("actor")
        if original_actor is not None and original_actor.user_id != owner:
            raise NotFound("画布写入账号不匹配")
        # Worker sessions are unscoped; this identity comes only from the durable task owner.
        self.session.info["actor"] = original_actor or ActorContext(
            owner, "", "", True, 0, "", f"assistant:{operation_id}"
        )
        try:
            return self._apply(
                project_id, source_key, batch, owner, operation_id, allowed_node_versions
            )
        finally:
            if original_actor is None:
                self.session.info.pop("actor", None)

    def _apply(self, project_id, source_key, batch, owner, operation_id, allowed):
        native = CanvasService(self.session)
        canvas = self.session.scalar(
            select(ProjectCanvas)
            .where(
                ProjectCanvas.project_id == project_id,
                ProjectCanvas.source_key == source_key,
                ProjectCanvas.archived_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if canvas is None:
            raise NotFound("目标画布不存在或无权访问")
        native._check_version(canvas, batch.expected_revision)
        document = native._document(canvas, private=True, lock=True, refresh=True)
        original = deepcopy(document)
        nodes = {item["id"]: item for item in document["nodes"]}
        before_nodes = {item["id"]: item for item in original["nodes"]}
        before_edges = {item["id"]: item for item in original["connections"]}
        revisions = self._versions(native, canvas)
        temporary, touched, new_ids, changed_inputs = {}, set(), [], set()
        deleting = {action.node for action in batch.actions if action.action == "delete_node"}

        def resolve(reference):
            key = temporary.get(reference[1:]) if reference.startswith("$") else reference
            if key not in nodes:
                raise NotFound("节点已移除或不属于目标画布")
            if key not in new_ids:
                if key not in allowed:
                    raise WorkflowError(
                        "assistant_write_scope", "只能操作获准节点或本任务成果", 403
                    )
                if allowed[key] != revisions[key]:
                    raise Conflict("节点正文、位置或私人参数已变化，请核对并主动刷新引用")
            if nodes[key]["type"] not in CANVAS_NODE_SPECS:
                raise WorkflowError("assistant_canvas_node_type", "助手尚未开放此节点类型", 422)
            touched.add(key)
            return key

        for index, action in enumerate(batch.actions):
            if action.action == "create_node":
                key = f"assistant-{operation_id}-{action.key}"
                if key in nodes:
                    raise Conflict("节点身份已存在，请核对原操作回执")
                values = action.values
                spec = CANVAS_NODE_SPECS[values.type]
                node = {
                    "id": key,
                    "type": values.type,
                    "title": values.title or spec["title"],
                    "position": {"x": 0, "y": 0},
                    "width": spec["width"],
                    "height": spec["height"],
                    "metadata": {"content": "", "status": "idle"},
                }
                if values.type == "text":
                    node["metadata"]["fontSize"] = 14
                else:
                    node["metadata"]["nodeRole"] = "generator"
                self._update(node, values.model_dump(exclude={"type", "title"}), owner)
                nodes[key] = node
                temporary[action.key] = key
                new_ids.append(key)
                revisions[key] = {"row_version": "1", "private_revision": "0"}
                touched.add(key)
                changed_inputs.add(key)
            elif action.action in {"connect_nodes", "disconnect_nodes"}:
                source, target = resolve(action.source), resolve(action.target)
                matches = [
                    edge
                    for edge in document["connections"]
                    if edge["fromNodeId"] == source and edge["toNodeId"] == target
                ]
                if len(matches) > 1 or any(
                    edge.get("relation") or edge.get("fromHandleId") or edge.get("toHandleId")
                    for edge in matches
                ):
                    raise Conflict("此连线含原生复合关系，请在画布中明确处理")
                if action.action == "disconnect_nodes":
                    document["connections"] = [
                        edge for edge in document["connections"] if edge not in matches
                    ]
                elif not matches:
                    pending, seen = [target], set()
                    while pending:
                        current = pending.pop()
                        if current == source:
                            raise WorkflowError(
                                "assistant_canvas_cycle", "连线会形成循环生成依赖", 422
                            )
                        if current not in seen:
                            seen.add(current)
                            pending.extend(
                                edge["toNodeId"]
                                for edge in document["connections"]
                                if edge["fromNodeId"] == current
                            )
                    document["connections"].append(
                        {
                            "id": f"assistant-{operation_id}-edge-{index}",
                            "fromNodeId": source,
                            "toNodeId": target,
                        }
                    )
                changed_inputs.add(target)
            else:
                key = resolve(action.node)
                if revisions[key] != {
                    "row_version": str(action.row_version),
                    "private_revision": str(action.private_revision),
                }:
                    raise Conflict("工具节点预期版本与可信范围不一致")
                if action.action == "update_node":
                    self._update(nodes[key], action.patch.model_dump(exclude_unset=True), owner)
                    changed_inputs.add(key)
                elif action.action == "move_node":
                    nodes[key]["position"] = action.position.model_dump()
                else:
                    for edge in document["connections"]:
                        if key in {edge["fromNodeId"], edge["toNodeId"]}:
                            for endpoint in (edge["fromNodeId"], edge["toNodeId"]):
                                if endpoint in allowed and allowed[endpoint] != revisions[endpoint]:
                                    raise Conflict("受删除影响的关联节点已变化，请主动刷新引用")
                    self._delete(nodes, document, key, set(allowed) | set(new_ids), deleting)
        for key in changed_inputs & set(nodes):
            sources = [
                nodes[edge["fromNodeId"]]
                for edge in document["connections"]
                if edge["toNodeId"] == key
            ]
            needs_profile = nodes[key]["type"] != "audio" and any(
                node_input_kind(source) in {"image", "video", "audio", "character"}
                for source in sources
            )
            check_canvas_inputs(
                nodes[key], sources, self._profile(nodes[key], owner) if needs_profile else None
            )
        new_ids = [key for key in new_ids if key in nodes]
        layout_new_nodes(nodes, document["connections"], new_ids)
        document["nodes"] = list(nodes.values())
        if document != original:
            self._check_version_ceiling(native, canvas, original, document)
            native._apply_document(canvas, CanvasDocument.model_validate(document))
        after_versions = self._versions(native, canvas)
        changes, refs = [], []
        before_order = {node["id"]: index for index, node in enumerate(original["nodes"])}
        after_order = {key: index for index, key in enumerate(nodes)}
        for key in sorted(nodes.keys() | before_nodes.keys()):
            before = (
                {
                    "document": before_nodes[key],
                    **revisions[key],
                    "deleted": False,
                    "z_index": before_order[key],
                }
                if key in before_nodes
                else None
            )
            after = {
                "document": nodes.get(key, before_nodes.get(key)),
                **after_versions.get(
                    key,
                    {
                        "row_version": str(int(revisions[key]["row_version"]) + 1),
                        "private_revision": "0",
                    },
                ),
                "deleted": key not in nodes,
                "z_index": after_order.get(key, before_order.get(key)),
            }
            if before != after:
                changes.append({"kind": "canvas_node", "id": key, "before": before, "after": after})
            if key not in touched:
                continue
            refs.append(
                {
                    "kind": "canvas_node",
                    "id": key,
                    "name": after["document"]["title"],
                    "project_id": str(project_id),
                    "canvas_id": source_key,
                    "row_version": after["row_version"],
                    "private_revision": after["private_revision"],
                    "status": "deleted"
                    if after["deleted"]
                    else "created"
                    if before is None
                    else "unchanged"
                    if before == after
                    else "updated",
                }
            )
        after_edges = {item["id"]: item for item in document["connections"]}
        before_positions = {edge["id"]: index for index, edge in enumerate(original["connections"])}
        after_positions = {edge["id"]: index for index, edge in enumerate(document["connections"])}
        changes.extend(
            {
                "kind": "canvas_edge",
                "id": key,
                "before": before_edges.get(key),
                "after": after_edges.get(key),
                "before_position": before_positions.get(key),
                "after_position": after_positions.get(key),
            }
            for key in sorted(before_edges.keys() | after_edges.keys())
            if before_edges.get(key) != after_edges.get(key)
        )
        return {
            "project_id": str(project_id),
            "canvas_id": source_key,
            "canvas_revision": str(canvas.row_version),
            "temporary_ids": temporary,
            "result_refs": sorted(refs, key=lambda item: item["id"]),
            "changes": changes,
        }

    def _check_version_ceiling(self, native, canvas, original, document):
        before, private_before = split_document(
            CanvasDocument.model_validate(original), strict_media=False
        )
        after, private_after = split_document(CanvasDocument.model_validate(document))
        if before != after and canvas.row_version == UINT64_MAX:
            raise Conflict("画布版本已达到上限")
        desired = {node["id"]: (index, node) for index, node in enumerate(after["nodes"])}
        for row in native.canvas_dao.children(CanvasNode, canvas.id, active=True, lock=True):
            if row.node_key not in desired:
                changed, content_changed = True, False
            else:
                index, node = desired[row.node_key]
                content = {
                    key: value
                    for key, value in node.items()
                    if key not in {"id", "type", "position", "width", "height", "parentId"}
                }
                content_changed = row.content_json != content or row.kind != node["type"]
                changed = content_changed or (
                    row.x != node["position"]["x"]
                    or row.y != node["position"]["y"]
                    or row.width != node["width"]
                    or row.height != node["height"]
                    or row.parent_node_key != node.get("parentId")
                    or row.z_index != index
                )
            if (
                changed
                and row.row_version == UINT64_MAX
                or (content_changed and row.content_version == UINT64_MAX)
            ):
                raise Conflict("节点版本已达到上限")
        for state in native.canvas_dao.children(CanvasNodeUserState, canvas.id, lock=True):
            if (
                state.node_key in private_after["nodes"]
                and private_before["nodes"].get(state.node_key)
                != private_after["nodes"][state.node_key]
                and state.row_version == UINT64_MAX
            ):
                raise Conflict("节点私人版本已达到上限")

    def _versions(self, native, canvas):
        private = {
            row.node_key: str(row.row_version)
            for row in native.canvas_dao.children(
                CanvasNodeUserState, canvas.id, lock=True, refresh=True
            )
        }
        return {
            row.node_key: {
                "row_version": str(row.row_version),
                "private_revision": private.get(row.node_key, "0"),
            }
            for row in native.canvas_dao.children(
                CanvasNode, canvas.id, active=True, lock=True, refresh=True
            )
        }

    def _model(self, identifier, kind, owner):
        model = self.session.scalar(
            select(AIModelConfig)
            .where(
                AIModelConfig.id == int(identifier),
                AIModelConfig.owner_user_id == owner,
                AIModelConfig.service_type == kind,
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if model is None:
            raise NotFound("节点所选本人模型不可用或类型不匹配")
        return model

    def _update(self, node, values, owner):
        metadata = node["metadata"]
        if "title" in values:
            node["title"] = values["title"]
        if "content" in values:
            if node["type"] != "text" and values["content"]:
                raise WorkflowError(
                    "assistant_canvas_media_identity", "不能伪造或覆盖媒体结果", 422
                )
            if node["type"] == "text":
                if metadata.get("content") != values["content"]:
                    metadata.pop("richText", None)
                metadata["content"] = values["content"]
        if "prompt" in values:
            metadata["prompt"] = values["prompt"]
        if "model_config_id" in values:
            model = (
                self._model(values["model_config_id"], node["type"], owner)
                if values["model_config_id"] is not None
                else None
            )
            metadata["model"] = f"host-{model.id}::{model.model_key}" if model else ""
        for key, value in (values.get("settings") or {}).items():
            if key not in _SETTINGS[node["type"]] and value is not None:
                raise WorkflowError(
                    "assistant_canvas_settings", "节点设置与实际节点类型不匹配", 422
                )
            if key in _SETTINGS[node["type"]]:
                if value is None:
                    metadata.pop(key, None)
                else:
                    metadata[key] = value

    def _profile(self, node, owner):
        selection = node["metadata"].get("model", "")
        if not isinstance(selection, str):
            raise Conflict("节点模型选择无效，请先在原生画布中核对")
        match = re.match(r"^host-([1-9][0-9]*)::", selection)
        if match is None:
            return None
        model = self._model(match[1], node["type"], owner)
        if selection != f"host-{model.id}::{model.model_key}":
            raise Conflict("节点所选模型已变化，请先核对并重新选择模型")
        return model.runtime_profile

    @staticmethod
    def _delete(nodes, document, key, allowed, deleting=()):
        incident = [
            edge
            for edge in document["connections"]
            if key in {edge["fromNodeId"], edge["toNodeId"]}
        ]
        dependencies = {
            endpoint for edge in incident for endpoint in (edge["fromNodeId"], edge["toNodeId"])
        } - {key}
        dependencies.update(
            identifier for identifier, node in nodes.items() if node.get("parentId") == key
        )
        references = {
            identifier
            for identifier, node in nodes.items()
            if identifier != key
            and identifier not in deleting
            and key in canvas_node_references(node.get("metadata", {}))
        }
        areas = [
            area
            for area in ("directorScenes", "timeline")
            if key in canvas_node_references(document.get(area))
        ]
        unresolved = dependencies - allowed | references
        if unresolved or areas:
            raise WorkflowError(
                "assistant_delete_impact",
                "删除节点会影响关联节点、导演场景或时间线，请先明确处理引用",
                409,
                details={"node_ids": sorted(unresolved), **({"areas": areas} if areas else {})},
            )
        if any(
            node.get("parentId") == key and identifier not in deleting
            for identifier, node in nodes.items()
        ):
            raise Conflict("节点仍有子节点，请先明确处理父子关系")
        document["connections"] = [edge for edge in document["connections"] if edge not in incident]
        del nodes[key]


def canvas_node_references(value, *, reference=False) -> set[str]:
    if isinstance(value, str):
        return {value} if reference else set()
    if isinstance(value, list):
        return set().union(*(canvas_node_references(item, reference=reference) for item in value))
    if isinstance(value, dict):
        result = set()
        for field, item in value.items():
            normalized = field.lower().replace("_", "")
            result.update(
                canvas_node_references(
                    item,
                    reference=reference or normalized.endswith(("nodeid", "nodeids")),
                )
            )
            if field.startswith("field:") and len(field.split(":")) > 2:
                result.add(field.removeprefix("field:").rsplit(":", 1)[0])
        return result
    return set()
