"""Existing frozen scope and source rules, kept with their actual responsibility."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.agent.context.canvas_scope import _successful_operations
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Project, ProjectCanvas
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.tasks.task import AgentTask
from short_drama.domain.canvas.document.canvas import CanvasNode, CanvasNodeUserState
from short_drama.schemas.assistant.operations.tool_inputs import ReadTaskResults


def read_canvas_results_locked(
    session: Session, conversation: AgentConversation, task: AgentTask, values: ReadTaskResults
) -> dict[str, Any]:
    prior = _successful_operations(session, task)
    operation = next((item for item in prior if item.id == values.operation_id), None)
    if operation is not None and (operation.result or {}).get("already_applied"):
        original_id = operation.result.get("operation_id")
        operation = next((item for item in prior if str(item.id) == original_id), None)
    if operation is None:
        raise NotFound("本任务没有此成功的画布操作回执")
    created = {
        (item.result["project_id"], item.result["canvas_id"], change["id"])
        for item in prior
        for change in item.changes
        if change["kind"] == "canvas_node" and change["before"] is None
    }
    target = operation.result
    visible = {
        ref["id"]
        for ref in operation.result_refs
        if ref["kind"] == "canvas_node"
        and (target["project_id"], target["canvas_id"], ref["id"]) in created
    }
    changes = {
        item["id"]: item["after"]
        for item in operation.changes
        if item["kind"] == "canvas_node"
        and item["id"] in visible
        and item["before"] is None
        and not item["after"]["deleted"]
    }
    requested = list(values.ids) or list(changes)
    if len(set(requested)) != len(requested):
        raise WorkflowError("invalid_tool_arguments", "成果引用不能重复", 422)
    if not requested or any(key not in changes for key in requested):
        raise WorkflowError("assistant_result_scope", "只能读取回执中的本任务真实新建节点", 403)
    canvas = session.scalar(
        select(ProjectCanvas)
        .join(Project, Project.id == ProjectCanvas.project_id)
        .where(
            Project.id == int(target["project_id"]),
            Project.owner_user_id == conversation.owner_user_id,
            Project.workspace_mode == "infinite_canvas",
            Project.archived_at.is_(None),
            ProjectCanvas.source_key == target["canvas_id"],
            ProjectCanvas.archived_at.is_(None),
        )
    )
    if canvas is None:
        raise NotFound("成果画布已删除或不再可访问")
    result, remaining, continuation = [], values.character_limit, None
    for index, key in enumerate(requested):
        current = session.execute(
            select(CanvasNode.row_version, CanvasNodeUserState.row_version)
            .outerjoin(
                CanvasNodeUserState,
                (CanvasNodeUserState.canvas_id == CanvasNode.canvas_id)
                & (CanvasNodeUserState.node_key == CanvasNode.node_key)
                & (CanvasNodeUserState.user_id == conversation.owner_user_id),
            )
            .where(
                CanvasNode.canvas_id == canvas.id,
                CanvasNode.node_key == key,
                CanvasNode.archived_at.is_(None),
            )
        ).first()
        if current is None:
            raise NotFound("成果节点已删除或不再可访问")
        saved = changes[key]
        node = saved["document"]
        metadata = node.get("metadata", {})
        fields = {
            "type": node["type"],
            "title": node["title"],
            "position": node["position"],
            "content": metadata.get("content", ""),
            "prompt": metadata.get("prompt", ""),
            "model": metadata.get("model", ""),
            "settings": {
                field: metadata[field]
                for field in (
                    "size",
                    "quality",
                    "seconds",
                    "vquality",
                    "audioVoice",
                    "audioFormat",
                    "audioSpeed",
                    "audioInstructions",
                )
                if field in metadata
            },
        }
        text = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        offset = values.character_offset if index == 0 else 0
        content = text[offset : offset + remaining]
        end = min(offset + len(content), len(text))
        result.append(
            {
                "kind": "canvas_node",
                "id": key,
                "project_id": target["project_id"],
                "canvas_id": target["canvas_id"],
                "row_version": saved["row_version"],
                "private_revision": saved["private_revision"],
                "current_revision": str(current[0]),
                "current_private_revision": str(current[1] or 0),
                "source_changed": (str(current[0]), str(current[1] or 0))
                != (saved["row_version"], saved["private_revision"]),
                "content": content,
                "range": {"offset": offset, "length": len(content), "total": len(text)},
            }
        )
        remaining -= len(content)
        if end < len(text) or remaining == 0 and index + 1 < len(requested):
            continuation = {
                "operation_id": str(operation.id),
                "ids": requested[index:] if end < len(text) else requested[index + 1 :],
                "character_offset": end if end < len(text) else 0,
            }
            break
    return {"operation_id": str(operation.id), "results": result, "next": continuation}
