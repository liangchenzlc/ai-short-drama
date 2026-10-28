from sqlalchemy import select

from short_drama.agent.context.attachments import (
    attachment_keys,
    available_attachment_ids,
    task_attachments,
)
from short_drama.agent.context.subjects import snapshot_subjects, subject_memory_keys
from short_drama.agent.core.fence import owns_execution as owns_execution
from short_drama.agent.harness import ENGINE_VERSION
from short_drama.agent.memory.context import history_projection_locked
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.tools.canvas import (
    canvas_requests_locked,
    canvas_result_keys,
)
from short_drama.agent.tools.contracts import (
    CANVAS_TOOL,
    IMAGE_PRIMARY_TOOL,
    MEDIA_STATUS_TOOL,
    MEDIA_TOOL,
    STANDARD_TOOL,
)
from short_drama.agent.tools.contracts import MEMORY_READ_LIMIT as MEMORY_READ_LIMIT
from short_drama.agent.tools.contracts import TOOL_SCHEMAS as TOOL_SCHEMAS
from short_drama.agent.tools.contracts import DescribeTarget as DescribeTarget
from short_drama.agent.tools.contracts import MemoryCatalog as MemoryCatalog
from short_drama.agent.tools.contracts import ReadHistory as ReadHistory
from short_drama.agent.tools.contracts import ReadMemory as ReadMemory
from short_drama.agent.tools.contracts import ReadReceipt as ReadReceipt
from short_drama.agent.tools.contracts import ReadSelectedSubjects as ReadSelectedSubjects
from short_drama.agent.tools.contracts import SearchMemory as SearchMemory
from short_drama.agent.tools.contracts import tool_manifest as tool_manifest
from short_drama.agent.tools.image import (
    image_primary_requests_locked,
)
from short_drama.agent.tools.media import (
    media_requests_locked,
)
from short_drama.agent.tools.standard import (
    standard_requests_locked,
    standard_result_keys,
)
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import AIModelConfig, Project, ProjectCanvas
from short_drama.service.assistant.context.subject import read_selected_snapshot, selection_metadata
from short_drama.service.assistant.operations.canvas import CANVAS_NODE_SPECS
from short_drama.service.assistant.tasks.policy import generation_policy_locked


def _describe(session, conversation, task, values):
    target = task.execution_target
    harness = task.effective_state.get("engine") == ENGINE_VERSION
    if target is None:
        return {"target": None, "write_authorized": False}
    project = session.scalar(
        select(Project).where(
            Project.id == int(target["project_id"]),
            Project.owner_user_id == conversation.owner_user_id,
            Project.archived_at.is_(None),
            Project.workspace_mode == target["mode"],
        )
    )
    if project is None:
        raise NotFound("目标作品不存在或无权访问")
    result = {
        "target": target,
        "name": project.name,
        "content_version": str(project.content_version),
    }
    if target["mode"] == "standard":
        result["storyboard_version"] = str(project.storyboard_version)
        requests = [] if harness else standard_requests_locked(session, conversation, task)
        result["write_authorized"] = bool(requests)
        result["available_tools"] = [STANDARD_TOOL] if requests else []
        primary_requests = (
            [] if harness else image_primary_requests_locked(session, conversation, task)
        )
        if primary_requests:
            result["write_authorized"] = True
            result["available_tools"].append(IMAGE_PRIMARY_TOOL)
        if (
            not harness
            and media_requests_locked(session, conversation, task)
            and generation_policy_locked(
                session, conversation.owner_user_id
            ).media_generation_enabled
        ):
            result["write_authorized"] = True
            result["available_tools"].append(MEDIA_TOOL)
        models = session.scalars(
            select(AIModelConfig)
            .where(
                AIModelConfig.owner_user_id == conversation.owner_user_id,
                AIModelConfig.service_type == "image",
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
            .order_by(AIModelConfig.id)
            .offset(values.model_offset)
            .limit(values.model_limit + 1)
        ).all()
        result["image_models"] = [
            {
                "id": str(model.id),
                "name": model.name,
                "model_key": model.model_key,
                "row_version": str(model.row_version),
            }
            for model in models[: values.model_limit]
        ]
        result["next_model_offset"] = (
            values.model_offset + values.model_limit if len(models) > values.model_limit else None
        )
        video_models = session.scalars(
            select(AIModelConfig)
            .where(
                AIModelConfig.owner_user_id == conversation.owner_user_id,
                AIModelConfig.service_type == "video",
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
            .order_by(AIModelConfig.id)
            .offset(values.model_offset)
            .limit(values.model_limit + 1)
        ).all()
        result["video_models"] = [
            {
                "id": str(model.id),
                "name": model.name,
                "model_key": model.model_key,
                "row_version": str(model.row_version),
            }
            for model in video_models[: values.model_limit]
        ]
        result["next_video_model_offset"] = (
            values.model_offset + values.model_limit
            if len(video_models) > values.model_limit
            else None
        )
        audio_models = session.scalars(
            select(AIModelConfig)
            .where(
                AIModelConfig.owner_user_id == conversation.owner_user_id,
                AIModelConfig.service_type == "audio",
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
            .order_by(AIModelConfig.id)
            .offset(values.model_offset)
            .limit(values.model_limit + 1)
        ).all()
        result["audio_models"] = [
            {
                "id": str(model.id),
                "name": model.name,
                "model_key": model.model_key,
                "row_version": str(model.row_version),
            }
            for model in audio_models[: values.model_limit]
        ]
        result["next_audio_model_offset"] = (
            values.model_offset + values.model_limit
            if len(audio_models) > values.model_limit
            else None
        )
    if target.get("canvas_id"):
        canvas = session.scalar(
            select(ProjectCanvas).where(
                ProjectCanvas.project_id == project.id,
                ProjectCanvas.source_key == target["canvas_id"],
                ProjectCanvas.archived_at.is_(None),
            )
        )
        if canvas is None:
            raise NotFound("目标画布不存在或无权访问")
        result["canvas_revision"] = str(canvas.row_version)
        requests = [] if harness else canvas_requests_locked(session, conversation, task)
        media = [] if harness else media_requests_locked(session, conversation, task)
        policy = generation_policy_locked(session, conversation.owner_user_id)
        result["write_authorized"] = bool(requests or (media and policy.media_generation_enabled))
        result["available_tools"] = [CANVAS_TOOL] if requests else []
        if media and policy.media_generation_enabled:
            result["available_tools"].extend([MEDIA_TOOL, MEDIA_STATUS_TOOL])
        result["node_specs"] = {"version": 1, "types": CANVAS_NODE_SPECS}
        models = session.scalars(
            select(AIModelConfig)
            .where(
                AIModelConfig.owner_user_id == conversation.owner_user_id,
                AIModelConfig.service_type.in_({"text", "image", "video", "audio"}),
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
            .order_by(AIModelConfig.id)
            .offset(values.model_offset)
            .limit(values.model_limit + 1)
        ).all()
        result["models"] = []
        for model in models[: values.model_limit]:
            profile = model.runtime_profile or {}
            specs = profile.get("logical_capability_profiles") or [
                profile.get("logical_capability_spec")
            ]
            capability = (profile.get("capability_config") or {}).get(model.service_type)
            references = capability.get("references") if isinstance(capability, dict) else None
            result["models"].append(
                {
                    "id": str(model.id),
                    "name": model.name,
                    "type": model.service_type,
                    "model_key": model.model_key,
                    "row_version": str(model.row_version),
                    "references": {
                        field: value
                        for field, value in (references or {}).items()
                        if field in {"maxImages", "maxVideos", "maxAudios"}
                        and type(value) is int
                        and 0 <= value <= 100
                    }
                    if isinstance(references, dict)
                    else {},
                    "input_profiles": [
                        {
                            "capability": spec.get("capability"),
                            "inputs": {
                                kind: {
                                    field: bounds[field]
                                    for field in ("min", "max")
                                    if type(bounds.get(field)) is int and 0 <= bounds[field] <= 100
                                }
                                for kind, bounds in (
                                    spec["inputs"] if isinstance(spec.get("inputs"), dict) else {}
                                ).items()
                                if kind in {"text", "image", "video", "audio"}
                                and isinstance(bounds, dict)
                            },
                        }
                        for spec in specs[:32]
                        if isinstance(spec, dict)
                    ],
                    "profiles_truncated": len(specs) > 32,
                }
            )
        result["next_model_offset"] = (
            values.model_offset + values.model_limit if len(models) > values.model_limit else None
        )
    if harness:
        from short_drama.agent.tools.autonomous import describe_scope
        from short_drama.agent.tools.registry import tool_manifest
        from short_drama.agent.tools.results import result_sources_locked

        enabled = generation_policy_locked(
            session, conversation.owner_user_id
        ).media_generation_enabled
        result.update(describe_scope(session, task))
        result["result_sources"] = result_sources_locked(
            session, task, before_id=values.result_before_id, limit=values.result_limit
        )
        result["write_authorized"] = True
        result["available_tools"] = [
            item["name"] for item in tool_manifest(task, media_enabled=enabled)
        ]
    return result


def _read_selected(session, conversation, task, values):
    source = task.context_snapshot
    if source is None:
        raise WorkflowError("selection_required", "本任务尚未选入参考主体", 409)
    return read_selected_snapshot(session, conversation.owner_user_id, source, values)


def _read_history(session, conversation, task, values, budget):
    end = min(values.to_seq or conversation.next_message_seq - 1, conversation.next_message_seq - 1)
    if end < values.from_seq:
        return {"items": [], "next": None}
    metadata = selection_metadata(session, conversation.owner_user_id, task.context_snapshot)
    available = {item["subject_key"] for item in metadata if item["availability"] != "unavailable"}
    keys = subject_memory_keys(
        {
            "schema_version": 2,
            "subjects": [
                subject
                for subject in snapshot_subjects(task.context_snapshot)
                if subject["subject_key"] in available
            ],
        }
    )
    restricted_ids, _ = restricted_messages(session, conversation.id)
    keys.update(
        attachment_keys(
            available_attachment_ids(
                session,
                conversation,
                task_attachments(session, task, restricted_ids=restricted_ids),
            )
        )
    )
    keys.update(standard_result_keys(session, conversation.owner_user_id, task))
    keys.update(canvas_result_keys(session, conversation.owner_user_id, task))
    page = history_projection_locked(
        session,
        conversation,
        task,
        values.from_seq,
        end,
        limit=values.limit,
        allowed_subject_keys=keys,
    )
    result = {
        "items": [],
        "next": {"from_seq": page["next_seq"], "character_offset": 0} if page["next_seq"] else None,
    }
    for item in page["items"]:
        offset = values.character_offset if item["seq"] == values.from_seq else 0
        content = item["content"][offset : offset + budget]
        result["items"].append(
            {
                **item,
                "content": content,
                "range": {"offset": offset, "length": len(content), "total": len(item["content"])},
            }
        )
        budget -= len(content)
        if offset + len(content) < len(item["content"]):
            result["next"] = {"from_seq": item["seq"], "character_offset": offset + len(content)}
            break
    return result
