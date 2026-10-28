"""Resolve task-produced reference images from durable receipts and native output identities."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AsyncTask,
    MediaAsset,
    MediaFile,
    Project,
    ProjectCanvas,
    ShotScript,
)
from short_drama.domain.assistant.operations.media import AgentMediaRequest
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.domain.canvas.document.canvas import CanvasNode


def _source_available(session: Session, owner: int, target: dict[str, Any], *, lock: bool) -> bool:
    project_id = int(target["project_id"])
    mode = "infinite_canvas" if target["kind"] == "node" else "standard"
    project = select(Project.id).where(
        Project.id == project_id,
        Project.owner_user_id == owner,
        Project.workspace_mode == mode,
        Project.archived_at.is_(None),
    )
    if session.scalar(project.with_for_update() if lock else project) is None:
        return False
    if target["kind"] == "node":
        source = (
            select(CanvasNode.id)
            .join(ProjectCanvas, ProjectCanvas.id == CanvasNode.canvas_id)
            .where(
                ProjectCanvas.project_id == project_id,
                ProjectCanvas.source_key == target["canvas_id"],
                ProjectCanvas.archived_at.is_(None),
                CanvasNode.node_key == target["id"],
                CanvasNode.archived_at.is_(None),
            )
        )
    elif target["kind"] in {"asset", "shot"}:
        model = Asset if target["kind"] == "asset" else ShotScript
        source = select(model.id).where(
            model.id == int(target["id"]),
            model.project_id == project_id,
            model.deleted_at.is_(None),
        )
    else:
        return False
    return session.scalar(source.with_for_update() if lock else source) is not None


def generated_image_references(
    session: Session,
    owner: int,
    task_id: int,
    media_ids: set[int],
    *,
    lock: bool = False,
) -> dict[int, dict[str, str | int]]:
    """A model-supplied ID alone never grants access to an output or its source content."""
    if not media_ids:
        return {}
    query = (
        select(AgentMediaRequest, MediaAsset.output_index, MediaFile.id)
        .join(AgentOperation, AgentOperation.id == AgentMediaRequest.operation_id)
        .join(AsyncTask, AsyncTask.id == AgentMediaRequest.generation_task_id)
        .join(AIGenerationRecord, AIGenerationRecord.task_id == AsyncTask.id)
        .join(MediaAsset, MediaAsset.record_id == AIGenerationRecord.id)
        .join(MediaFile, MediaFile.id == MediaAsset.media_id)
        .where(
            AgentMediaRequest.task_id == task_id,
            AgentOperation.task_id == task_id,
            AgentOperation.tool_name == "submit_generation_batch",
            AgentOperation.status.in_(("succeeded", "failed", "conflict")),
            AgentMediaRequest.media_kind == "image",
            AgentMediaRequest.status.in_(("succeeded", "failed", "conflict")),
            AgentMediaRequest.result.is_not(None),
            AsyncTask.initiated_by == owner,
            AsyncTask.service_type == "image",
            AIGenerationRecord.status == "succeeded",
            AIGenerationRecord.config_snapshot["agent_managed"].as_boolean().is_(True),
            MediaAsset.media_type == "image",
            MediaFile.id.in_(media_ids),
            MediaFile.created_by == owner,
            MediaFile.project_id == AsyncTask.project_id,
            MediaFile.storage_locator.startswith("minio://"),
            MediaFile.format_code.startswith("image/"),
        )
        .order_by(AgentMediaRequest.id, MediaAsset.output_index)
    )
    rows = session.execute(query.with_for_update() if lock else query).all()
    result = {}
    for intent, output_index, media_id in rows:
        if media_id in result:
            continue
        saved = any(
            entry.get("media_id") == str(media_id)
            and entry.get("output_index") == output_index
            and entry.get("status") == "ready"
            for entry in (intent.result or {}).get("items", [])
        )
        if not saved or not _source_available(session, owner, intent.frozen_target, lock=lock):
            continue
        result[media_id] = {
            "media_id": str(media_id),
            "request_id": str(intent.id),
            "generation_id": str(intent.generation_task_id),
            "output_index": output_index,
        }
    return result
