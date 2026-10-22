"""Merge selected inverse graph changes through the native canvas save boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import Conflict
from short_drama.domain import Project, ProjectCanvas
from short_drama.domain.agent_task import AgentOperation
from short_drama.domain.agent_undo import AgentBatchUndo
from short_drama.domain.canvas import CanvasNode
from short_drama.schemas.base import UINT64_MAX
from short_drama.schemas.canvas import CanvasDocument
from short_drama.service.assistant_canvas_service import (
    AssistantCanvasService,
    canvas_node_references,
    check_canvas_inputs,
    layout_new_nodes,
    node_input_kind,
)
from short_drama.service.assistant_undo_chain_service import advance_document, advance_snapshot
from short_drama.service.canvas_service import CanvasService


def canvas_undo_context(
    session: Session, project: Project, operation: AgentOperation, receipts: list[AgentBatchUndo]
) -> dict[str, Any] | None:
    canvas = session.scalar(
        select(ProjectCanvas)
        .where(
            ProjectCanvas.project_id == project.id,
            ProjectCanvas.source_key == (operation.result or {}).get("canvas_id"),
            ProjectCanvas.archived_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if canvas is None:
        return None
    native, adapter = CanvasService(session), AssistantCanvasService(session)
    document = native._document(canvas, private=True, lock=True, refresh=True)
    versions = adapter._versions(native, canvas)
    rows = {
        row.node_key: row
        for row in native.canvas_dao.children(CanvasNode, canvas.id, lock=True, refresh=True)
    }
    expected = advance_document(
        {"row_version": str((operation.result or {}).get("canvas_revision"))},
        "canvas_document",
        canvas.source_key,
        receipts,
        operation.id,
    )["row_version"]
    return {
        "native": native,
        "adapter": adapter,
        "canvas": canvas,
        "document": document,
        "versions": versions,
        "rows": rows,
        "expected_revision": expected,
        "receipts": receipts,
        "operation_id": operation.id,
    }


def node_snapshots(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if "snapshots" in context:
        return context["snapshots"]
    context["snapshots"] = {
        node["id"]: {
            "document": deepcopy(node),
            **context["versions"][node["id"]],
            "deleted": False,
            "z_index": index,
        }
        for index, node in enumerate(context["document"]["nodes"])
    }
    return context["snapshots"]


def preview_canvas_change(
    context: dict[str, Any] | None, change: dict[str, Any], changes: dict[str, dict[str, Any]]
) -> tuple[str | None, dict[str, Any] | None, set[str]]:
    if context is None:
        return "原画布已不可用", None, set()
    canvas, document = context["canvas"], context["document"]
    requires, reason = set(), None
    nodes = {node["id"]: node for node in document["nodes"]}
    edges = {edge["id"]: edge for edge in document["connections"]}
    key, before, after = change["id"], change["before"], change["after"]
    if change["kind"] == "canvas_edge":
        current = edges.get(key)
        if current != after:
            reason = "连线已有后续变化"
        if before:
            if change.get("before_position") is None:
                reason = "旧回执缺少连线顺序，无法证明可以安全恢复"
            requires.update(
                item_key
                for item_key, item in changes.items()
                if item["kind"] == "canvas_edge" and item["id"] != key
            )
            for endpoint in (before["fromNodeId"], before["toNodeId"]):
                dependency = f"canvas_node:{endpoint}"
                if endpoint not in nodes:
                    restore = changes.get(dependency, {}).get("before")
                    if restore and not restore["deleted"]:
                        requires.add(dependency)
                    else:
                        reason = "原连线端点已不可用"
    else:
        expected = advance_snapshot(change, context["receipts"], context["operation_id"])
        current = node_snapshots(context).get(key)
        if current is None and expected and expected["deleted"]:
            row = context["rows"].get(key)
            if (
                row
                and row.archived_at is not None
                and str(row.row_version) == expected["row_version"]
            ):
                current = deepcopy(expected)
        if current != expected:
            reason = "节点正文、位置、媒体或私人参数已有后续变化"
        row = context["rows"].get(key)
        if row is not None and row.row_version == UINT64_MAX:
            reason = "节点版本已达到上限"
        if before is None:
            for edge in edges.values():
                if key in (edge["fromNodeId"], edge["toNodeId"]):
                    dependency = f"canvas_edge:{edge['id']}"
                    if dependency in changes and changes[dependency]["before"] is None:
                        requires.add(dependency)
                    else:
                        reason = "新节点仍被后续连线引用"
            for identifier, node in nodes.items():
                if identifier != key and (
                    node.get("parentId") == key
                    or key in canvas_node_references(node.get("metadata", {}))
                ):
                    dependency = f"canvas_node:{identifier}"
                    if dependency in changes and changes[dependency]["before"] is None:
                        requires.add(dependency)
                    else:
                        reason = "新节点仍被后续节点引用"
            if any(
                key in canvas_node_references(document.get(area))
                for area in ("timeline", "directorScenes")
            ):
                reason = "新节点仍被时间线或导演场景引用"
        else:
            references = canvas_node_references(before["document"].get("metadata", {}))
            if before["document"].get("parentId"):
                references.add(before["document"]["parentId"])
            for identifier in references - {key} - nodes.keys():
                dependency = f"canvas_node:{identifier}"
                restore = changes.get(dependency, {}).get("before")
                if restore and not restore["deleted"]:
                    requires.add(dependency)
                else:
                    reason = "原节点引用已不可用"
        ordering = {
            item_key
            for item_key, item in changes.items()
            if item["kind"] == "canvas_node"
            and item["before"]
            and (item["after"]["deleted"] or item["before"]["z_index"] != item["after"]["z_index"])
        }
        if f"canvas_node:{key}" in ordering:
            requires.update(ordering - {f"canvas_node:{key}"})
    if str(canvas.row_version) != context["expected_revision"]:
        reason = "画布结构已有后续变化，请保留当前节点与连线"
    return reason, {"snapshot": current, "canvas_revision": str(canvas.row_version)}, requires


def restore_canvas_locked(
    service: Any,
    project: Project,
    context: dict[str, Any],
    changes: dict[str, dict[str, Any]],
    selected: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    native, adapter, canvas = context["native"], context["adapter"], context["canvas"]
    original = context["document"]
    document = deepcopy(original)
    nodes = {node["id"]: node for node in document["nodes"]}
    edges = {edge["id"]: edge for edge in document["connections"]}
    order = {node["id"]: index for index, node in enumerate(document["nodes"])}
    edge_order = {edge["id"]: index for index, edge in enumerate(document["connections"])}
    for key in sorted(selected):
        change = changes[key]
        before, identifier = change["before"], change["id"]
        if change["kind"] == "canvas_edge":
            if before is None:
                edges.pop(identifier, None)
            else:
                edges[identifier] = deepcopy(before)
                edge_order[identifier] = change["before_position"]
        elif before is None:
            nodes.pop(identifier, None)
        else:
            nodes[identifier] = deepcopy(before["document"])
            order[identifier] = before["z_index"]
    document["nodes"] = sorted(nodes.values(), key=lambda node: (order[node["id"]], node["id"]))
    document["connections"] = sorted(
        edges.values(), key=lambda edge: (edge_order[edge["id"]], edge["id"])
    )
    owner = service._actor().user_id
    # Validate the merged graph, including partial inverses, before any native write.
    layout_new_nodes(deepcopy(nodes), document["connections"], list(nodes))
    affected = {changes[key]["id"] for key in selected if changes[key]["kind"] == "canvas_node"}
    affected.update(
        change["before"]["toNodeId"]
        for key, change in changes.items()
        if key in selected and change["kind"] == "canvas_edge" and change["before"]
    )
    for key in affected & nodes.keys():
        target = nodes.get(key)
        if target is None:
            raise Conflict("撤销后的连线端点不可用")
        sources = [
            nodes[edge["fromNodeId"]] for edge in document["connections"] if edge["toNodeId"] == key
        ]
        if target["type"] in {"text", "image", "video", "audio"}:
            profile = (
                adapter._profile(target, owner)
                if any(
                    node_input_kind(source) in {"image", "video", "audio", "character"}
                    for source in sources
                )
                and target["type"] != "audio"
                else None
            )
            check_canvas_inputs(target, sources, profile)
    adapter._check_version_ceiling(native, canvas, original, document)
    snapshots = deepcopy(node_snapshots(context))
    for key in selected:
        change = changes[key]
        if change["kind"] == "canvas_node" and change["after"]["deleted"]:
            snapshots[change["id"]] = advance_snapshot(
                change, context["receipts"], context["operation_id"]
            )
    previous_version = str(canvas.row_version)
    native._apply_document(canvas, CanvasDocument.model_validate(document))
    context["document"] = native._document(canvas, private=True, lock=True, refresh=True)
    context["versions"] = adapter._versions(native, canvas)
    context.pop("snapshots", None)
    after_nodes = node_snapshots(context)
    inverse, refs = [], []
    for identifier in sorted(snapshots.keys() | after_nodes.keys()):
        before, after = snapshots.get(identifier), after_nodes.get(identifier)
        if before == after:
            continue
        if after is None:
            row = context["rows"][identifier]
            after = {
                **deepcopy(before),
                "deleted": True,
                "row_version": str(row.row_version),
                "private_revision": "0",
            }
        inverse.append({"kind": "canvas_node", "id": identifier, "before": before, "after": after})
        if f"canvas_node:{identifier}" in selected:
            refs.append(
                {
                    "kind": "canvas_node",
                    "id": identifier,
                    "project_id": str(project.id),
                    "canvas_id": canvas.source_key,
                    "name": after["document"]["title"],
                    "row_version": after["row_version"],
                    "private_revision": after["private_revision"],
                    "status": "deleted" if after["deleted"] else "updated",
                }
            )
    for key in sorted(selected):
        change = changes[key]
        if change["kind"] == "canvas_edge":
            inverse.append(
                {
                    "kind": "canvas_edge",
                    "id": change["id"],
                    "before": change["after"],
                    "after": change["before"],
                }
            )
    inverse.append(
        {
            "kind": "canvas_document",
            "id": canvas.source_key,
            "before": {"row_version": previous_version},
            "after": {"row_version": str(canvas.row_version)},
        }
    )
    return inverse, refs
