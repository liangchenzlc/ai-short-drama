"""Existing frozen scope and source rules, kept with their actual responsibility."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.agent.context.standard_scope import _successful_operations
from short_drama.agent.context.subjects import snapshot_subjects
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Asset, Project, ShotScript
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.tasks.task import AgentTask
from short_drama.schemas.assistant.operations.tool_inputs import ReadTaskResults as ReadTaskResults
from short_drama.service.assistant.operations.dialogue import sound_line_state


def read_standard_results_locked(
    session: Session, conversation: AgentConversation, task: AgentTask, values: ReadTaskResults
) -> dict[str, Any]:
    """Read saved public fields from task-created or still-selected, task-updated objects."""
    prior = _successful_operations(session, task)
    operation = next((item for item in prior if item.id == values.operation_id), None)
    if operation is None:
        raise NotFound("本任务没有此成功的标准操作回执")
    if (operation.result or {}).get("already_applied"):
        original = (operation.result or {}).get("operation_id")
        operation = next((item for item in prior if str(item.id) == original), None)
        if operation is None:
            raise NotFound("本任务的原始标准操作回执不可用")
    created = {
        (change["kind"], change["id"])
        for item in prior
        for change in item.changes
        if change["before"] is None
    }
    readable = created | {
        (item["kind"], item["id"])
        for item in snapshot_subjects(task.context_snapshot)
        if item["source_kind"] == "project"
        and item["project_id"] == str((operation.result or {}).get("project_id"))
        and item["kind"] in {"asset", "shot"}
    }
    visible = {(item["kind"], item["id"]) for item in operation.result_refs} & readable
    changes = {
        change["id"]: change
        for change in operation.changes
        if (change["kind"], change["id"]) in visible
    }
    requested = [str(identifier) for identifier in values.ids] or list(changes)
    if len(set(requested)) != len(requested):
        raise WorkflowError("invalid_tool_arguments", "成果引用不能重复", 422)
    if not requested or any(identifier not in changes for identifier in requested):
        raise WorkflowError(
            "assistant_result_scope",
            "请求对象不在该保存批次的可读成果中，请核对原始保存 operation_id 和结果 ID",
            403,
        )
    project_id = (operation.result or {}).get("project_id")
    project = session.scalar(
        select(Project.id).where(
            Project.id == int(project_id),
            Project.owner_user_id == conversation.owner_user_id,
            Project.workspace_mode == "standard",
            Project.archived_at.is_(None),
        )
    )
    if project is None:
        raise NotFound("成果作品已删除或不再可访问")
    result, remaining, continuation = [], values.character_limit, None
    for index, identifier in enumerate(requested):
        change = changes[identifier]
        if change["kind"] == "sound_line":
            _fields, binding = sound_line_state(session, project, identifier)
            current_revision = binding["row_version"]
        else:
            model = Asset if change["kind"] == "asset" else ShotScript
            current_revision = session.scalar(
                select(model.row_version).where(
                    model.id == int(identifier),
                    model.project_id == project,
                    model.deleted_at.is_(None),
                )
            )
        if current_revision is None:
            raise NotFound("成果已删除或不再可访问")
        saved = change["after"]
        source_changed = str(current_revision) != saved["row_version"]
        if change["kind"] == "sound_line":
            source_changed = source_changed or any(
                binding[key] != saved[key]
                for key in ("assembly_version", "timeline_hash", "line_checksum")
            )
        fields = {
            key: value
            for key, value in saved.items()
            if key
            in {
                "name",
                "kind",
                "label",
                "description",
                "prompt",
                "tags",
                "scene_time",
                "state",
                "position",
                "title",
                "script",
                "duration_ms",
                "shot_size",
                "image_settings",
                "video_prompt",
                "video_settings",
                "asset_ids",
                "dialogue",
                "id",
                "character",
                "text",
                "voice",
                "config_id",
                "start_ms",
                "media_id",
                "adopted_hash",
            }
        }
        text = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        offset = values.character_offset if index == 0 else 0
        content = text[offset : offset + remaining]
        end = min(offset + len(content), len(text))
        result.append(
            {
                "id": identifier,
                "kind": change["kind"],
                "project_id": str(project),
                "row_version": saved["row_version"],
                "current_revision": str(current_revision),
                "source_changed": source_changed,
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
