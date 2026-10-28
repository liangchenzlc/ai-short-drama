"""Harness tool handlers share native services, ownership, versions and receipts."""

from pydantic import ValidationError
from sqlalchemy import select

from short_drama.agent.context.capabilities import input_capabilities
from short_drama.agent.context.working_set import record_locked as record_observation
from short_drama.agent.resources.catalog import attachments_locked
from short_drama.agent.resources.contracts import HydrationIntent
from short_drama.agent.resources.resolver import attachment_ref
from short_drama.agent.skill import progressive
from short_drama.agent.task.contract import declare_locked
from short_drama.agent.task.requirements import confirm_locked
from short_drama.agent.tools import canvas, image, media, standard
from short_drama.agent.tools import script as script_tools
from short_drama.agent.tools.contracts import ReadImageContent
from short_drama.agent.tools.registry import REGISTRY, tool_catalog
from short_drama.agent.tools.results import read_results_locked
from short_drama.agent.tools.scope import autonomous_scope
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.agent_memory_dao import AgentMemoryDAO
from short_drama.domain.agent import AgentMessage, AgentRun, AgentToolCall
from short_drama.domain.agent_memory import AgentMemoryItem
from short_drama.domain.agent_task import AgentOperation, AgentTaskInput
from short_drama.schemas.agent_memory import MemoryCreate
from short_drama.service.agent_memory_store import (
    apply_memory_command_locked,
    blocked_extraction,
    checked_scope,
    create_memory_locked,
    source_rows,
)
from short_drama.service.assistant_image_service import AssistantImageService


def _standard(session, conversation, task, operation, values, settings):
    request = autonomous_scope(session, conversation, task, operation)
    request["directive"] = standard.StandardDirective("create", {}, associate=True)
    if not task.execution_target or task.execution_target["mode"] != "standard":
        raise WorkflowError("assistant_tool_scope", "当前任务没有标准写入目标", 403)
    return standard.execute_standard_locked(
        session, conversation, task, operation, values, settings=settings, scope_request=request
    )


def _canvas(session, conversation, task, operation, values, settings):
    request = autonomous_scope(session, conversation, task, operation)
    request["directive"] = canvas.CanvasDirective("create", {}, connect=True)
    if not task.execution_target or task.execution_target["mode"] != "infinite_canvas":
        raise WorkflowError("assistant_tool_scope", "当前任务没有画布写入目标", 403)
    return canvas.execute_canvas_locked(
        session, conversation, task, operation, values, scope_request=request
    )


def _intent(session, reference, operation, values):
    video_audio = (
        session.scalar(
            select(AgentTaskInput.payload["video_audio"].as_string()).where(
                AgentTaskInput.task_id == operation.task_id,
                AgentTaskInput.input_revision == operation.input_revision,
            )
        )
        or "include"
    )
    return HydrationIntent(
        resource=reference,
        purpose=values.purpose,
        tool_call_id=session.get(AgentToolCall, operation.tool_call_id).provider_call_id,
        invocation_id=f"after-operation:{operation.id}",
        detail=values.detail,
        crop=values.crop,
        video_audio=video_audio,
    ).model_dump(mode="json")


def _read_resource(session, conversation, task, operation, values, settings):
    attachment = attachments_locked(session, conversation, task).get(values.ref_id)
    if attachment is None:
        raise NotFound(
            "附件目录中没有此 ref_id。Skill 的 references、scripts 和示例图请使用 "
            "read_skill_resource(activation_id, path)，身份须来自已加载方法的资源目录。"
        )
    reference = attachment_ref(attachment)
    if reference.kind in {"image", "audio", "video"}:
        run = session.get(AgentRun, operation.run_id)
        capabilities = input_capabilities(run.config_snapshot)
        intent = _intent(session, reference, operation, values)
        if (
            reference.kind == "audio"
            and not capabilities["audio"]
            or reference.kind in {"image", "video"}
            and not capabilities["image"]
            or reference.kind == "video"
            and intent["video_audio"] != "visual_only"
            and not capabilities["audio"]
        ):
            raise WorkflowError("agent_resource_modality", "模型不支持所需媒体理解模态", 422)
        return {
            "resource_ref": reference.model_dump(mode="json"),
            "hydration_intent": intent,
        }
    if reference.kind == "text":
        text = attachment.get("text_content", "")
        # text_content is the validated ingress decoding (UTF-8 BOM/GB18030
        # included). Its checksum is for the original bytes, not this decoding.
        end = min(values.offset + values.limit, len(text))
        return {
            "resource_ref": reference.model_dump(mode="json"),
            "content": text[values.offset : end],
            "offset": values.offset,
            "next_offset": end if end < len(text) else None,
        }
    return {
        "resource_ref": reference.model_dump(mode="json"),
        "error": {
            "code": "agent_resource_modality",
            "message": "当前资源需使用支持该模态的读取策略",
        },
    }


def _pixels(session, conversation, task, operation, values, settings):
    legacy_values = ReadImageContent.model_validate(
        values.model_dump(exclude={"purpose", "detail", "crop"})
    )
    result = image.read_image_content_locked(session, conversation, task, operation, legacy_values)
    run = session.get(AgentRun, operation.run_id)
    attachment = run.checkpoint["image_content_reads"][result["image_read_id"]]["attachment"]
    reference = attachment_ref(attachment, source="image_content_read")
    return {
        **result,
        "image_ref": reference.model_dump(mode="json"),
        "hydration_intent": _intent(session, reference, operation, values),
    }


def _load(session, conversation, task, operation, values, settings):
    return progressive.load_locked(session, conversation, task, operation, values, settings)


def _search(session, conversation, task, operation, values, settings):
    return progressive.catalog_locked(
        session,
        conversation.owner_user_id,
        task,
        settings,
        query=values.query,
        offset=values.offset,
        limit=values.limit,
        include_manual=False,
    )


def _enable_tools(session, conversation, task, operation, values, settings):
    from short_drama.service.assistant_policy_service import generation_policy_locked

    policy = generation_policy_locked(session, conversation.owner_user_id)
    catalog = tool_catalog(task, media_enabled=policy.media_generation_enabled)
    group = next((item for item in catalog if item["group"] == values.group), None)
    if group is None:
        raise WorkflowError("invalid_tool_arguments", "此能力组不适用于当前任务或尚未开启", 422)
    enabled = task.effective_state.get("enabled_tool_groups", [])
    task.effective_state = {
        **task.effective_state,
        "enabled_tool_groups": enabled if group["enabled"] else [*enabled, values.group],
    }
    return {"group": values.group, "tools": group["tools"], "already_applied": group["enabled"]}


def _primary(session, conversation, task, operation, values, settings):
    from short_drama.agent.context.subjects import snapshot_subjects

    subject = next(
        (
            item
            for item in snapshot_subjects(task.context_snapshot)
            if item["subject_key"] == values.subject_key
        ),
        None,
    )
    if (
        subject is None
        or not task.execution_target
        or task.execution_target["mode"] != "standard"
        or subject["project_id"] != task.execution_target["project_id"]
    ):
        raise WorkflowError("assistant_image_scope", "图片未选入当前写入目标", 403)
    catalog = image._updated_catalog(subject, image._prior(session, task))
    group = next((item for item in catalog["groups"] if item["id"] == str(values.group_id)), None)
    slot = next(
        (item for item in (group or {}).get("items", []) if item["id"] == str(values.item_id)), None
    )
    if slot is None or slot["status"] != "ready":
        raise Conflict("图位不存在或未就绪")
    trusted = {
        "subject_key": values.subject_key,
        "group_id": str(values.group_id),
        "item_id": str(values.item_id),
        "media_id": slot["media_id"],
        "target_kind": catalog["target_kind"],
        "target_id": catalog["target_id"],
        "expected_primary_version": group["primary_version"],
        "expected_item_version": slot["item_version"],
        "expected_target_version": catalog["target_version"],
        "is_current": group["is_current"],
    }
    result = AssistantImageService(session).set_primary_locked(
        conversation.owner_user_id,
        task.execution_target["project_id"],
        values,
        operation_id=operation.id,
        trusted=trusted,
    )
    operation.changes = result.pop("changes")
    operation.result_refs = result["result_refs"]
    return {**result, "operation_id": str(operation.id), "already_applied": False}


def _memory(session, conversation, task, operation, values, settings):
    scope = checked_scope(
        session, conversation.owner_user_id, conversation.id, task.memory_project_ids
    )
    identifiers = set(values.source_message_ids)
    permitted = set(
        session.scalars(
            select(AgentMessage.id)
            .join(
                AgentTaskInput,
                AgentTaskInput.message_id == AgentMessage.id,
            )
            .where(
                AgentTaskInput.task_id == task.id,
                AgentTaskInput.input_revision <= operation.input_revision,
                AgentTaskInput.applied_plan_revision.is_not(None),
                AgentMessage.role == "user",
                AgentMessage.conversation_id == conversation.id,
                AgentMessage.id.in_(identifiers),
            )
        )
    )
    if identifiers != permitted:
        raise WorkflowError("assistant_memory_sources", "记忆来源须为本任务已应用用户消息", 403)
    sources = source_rows(session, conversation.owner_user_id, conversation.id, list(identifiers))
    if any(item.get("memory_restricted") for item in sources):
        raise Conflict("已遗忘来源不能重新进入记忆")
    previous = session.scalar(
        select(AgentOperation)
        .where(
            AgentOperation.task_id == task.id,
            AgentOperation.tool_name == operation.tool_name,
            AgentOperation.payload_hash == operation.payload_hash,
            AgentOperation.status == "succeeded",
        )
        .limit(1)
    )
    if previous:
        operation.result_refs = previous.result_refs
        return {**previous.result, "already_applied": True, "operation_id": str(previous.id)}
    action = operation.tool_name.removesuffix("_memory")
    before = None
    if action == "remember":
        if values.scope_type == "project" and len(scope.project_ids) != 1:
            raise Conflict("作品记忆需要唯一明确绑定的作品")
        create = MemoryCreate(
            scope_type=values.scope_type,
            kind=values.kind,
            memory_key=values.memory_key,
            title=values.title,
            content=values.content,
            conversation_id=conversation.id if values.scope_type == "conversation" else None,
            project_id=scope.project_ids[0] if values.scope_type == "project" else None,
        )
        scope_key = (
            "user"
            if values.scope_type == "user"
            else str(
                conversation.id if values.scope_type == "conversation" else scope.project_ids[0]
            )
        )
        if blocked_extraction(
            session,
            conversation.owner_user_id,
            values.scope_type,
            scope_key,
            create.kind,
            create.memory_key,
            sources,
        ):
            raise Conflict("已遗忘记忆须用新的明确来源恢复")
        row = create_memory_locked(session, conversation.owner_user_id, create, sources=sources)
    else:
        row = session.scalar(
            AgentMemoryDAO(session)
            .rows(scope, active=False)
            .where(AgentMemoryItem.id == values.memory_id)
            .with_for_update()
        )
        if row is None:
            raise NotFound("记忆不存在或超出当前任务范围")
        if row.content_version != values.content_version:
            raise Conflict("记忆版本已变化，请读取当前版本")
        if action == "update" and not values.content:
            raise WorkflowError("invalid_tool_arguments", "更新记忆需要正文", 422)
        before = {
            "id": str(row.id),
            "content_version": str(row.content_version),
            "state": row.state,
        }
        apply_memory_command_locked(session, row, action, values.content, sources)
    session.flush()
    reference = {
        "kind": "memory",
        "id": str(row.id),
        "name": row.title,
        "scope_type": row.scope_type,
        "content_version": str(row.content_version),
        "state": row.state,
    }
    operation.result_refs = [reference]
    operation.changes = [
        {
            "kind": "memory",
            "id": str(row.id),
            "before": before,
            "after": reference,
            "source_message_ids": [str(item) for item in identifiers],
        }
    ]
    return {"memory": reference, "already_applied": False}


def _media(session, conversation, task, operation, values, settings):
    request = autonomous_scope(session, conversation, task, operation)
    if not task.execution_target:
        raise WorkflowError("assistant_tool_scope", "当前任务没有媒体写入目标", 403)
    allowed, versions, counts = {}, {}, {}
    native = False
    for item in values.requests:
        kind = media._target_kind(item)
        expected_mode = "infinite_canvas" if kind == "node" else "standard"
        if task.execution_target["mode"] != expected_mode:
            raise WorkflowError("assistant_generation_scope", "媒体目标与任务模式不符", 403)
        native_item = item.target_kind == "shot_video" and item.native_dialogue_version is not None
        directive = media.MediaDirective(
            kind, str(item.target_id), item.count, item.media_kind, native_item
        )
        targets, trusted = media._allowed(session, task, {**request, "directive": directive})
        if str(item.target_id) not in targets.get(kind, []):
            raise WorkflowError("assistant_generation_scope", "媒体目标不在选入或本任务成果内", 403)
        allowed.setdefault(kind, set()).add(str(item.target_id))
        versions.update(trusted)
        counts[kind, str(item.target_id)] = item.count
        native |= native_item
    request.update(
        directive=media.MediaDirective("batch", "", 1, values.requests[0].media_kind, native),
        allowed=allowed,
        versions=versions,
        counts=counts,
    )
    return media.reserve_batch_locked(
        session, conversation, task, operation, values, settings, scope_request=request
    )


HANDLERS = {
    "read_current_script": script_tools.read_locked,
    "propose_script_patch": script_tools.propose_locked,
    "confirm_requirements": confirm_locked,
    "set_task_requirements": declare_locked,
    "enable_tool_group": _enable_tools,
    "record_task_observation": record_observation,
    "release_skill": progressive.release_locked,
    "apply_standard_patch": _standard,
    "apply_canvas_patch": _canvas,
    "read_resource": _read_resource,
    "read_image_content": _pixels,
    "read_task_results": read_results_locked,
    "load_skill": _load,
    "search_skills": _search,
    "set_image_group_primary": _primary,
    "submit_generation_batch": _media,
    **{
        name: _memory
        for name in ("remember_memory", "update_memory", "forget_memory", "restore_memory")
    },
}


def execute_locked(session, conversation, task, operation, settings):
    autonomous_scope(session, conversation, task, operation)
    run = session.get(AgentRun, operation.run_id)
    if operation.tool_name not in (run.checkpoint or {}).get("available_tool_names", []):
        raise WorkflowError("unknown_tool", "此工具不在当前任务能力目录中", 422)
    schema, _ = REGISTRY[operation.tool_name]
    try:
        values = schema.model_validate(operation.arguments)
    except ValidationError as error:
        raise WorkflowError(
            "invalid_tool_arguments",
            "工具参数未通过契约校验，请根据字段错误修正",
            422,
            details={
                "validation_errors": error.errors(
                    include_input=False, include_context=False, include_url=False
                )[:8]
            },
        ) from None
    return HANDLERS[operation.tool_name](session, conversation, task, operation, values, settings)


def describe_scope(session, task):
    """Expose the same frozen identities and versions used by the write guard."""
    request = {"snapshot": task.context_snapshot}
    if task.execution_target["mode"] == "standard":
        request["directive"] = standard.StandardDirective("create", {}, associate=True)
        prior = standard._successful_operations(session, task) + standard._primary_operations(
            session, task
        )
        allowed, versions = standard._scope(task, request, prior)
        latest = session.scalar(
            select(AgentTaskInput)
            .where(
                AgentTaskInput.task_id == task.id,
                AgentTaskInput.input_revision <= task.applied_input_revision,
                AgentTaskInput.applied_plan_revision.is_not(None),
            )
            .order_by(AgentTaskInput.input_revision.desc())
            .limit(1)
        )
        lines, sound = set(), None
        if latest and latest.payload.get("sound_write_binding"):
            request["input"] = latest
            lines, sound = standard.sound_write_scope(task, request, prior)
        return {
            "allowed_assets": [
                {"id": key, "row_version": str(versions["asset", key])}
                for key in sorted(allowed["asset"], key=int)
            ],
            "allowed_shots": [
                {"id": key, "row_version": str(versions["shot", key])}
                for key in sorted(allowed["shot"], key=int)
            ],
            "allowed_sound_lines": sorted(lines),
            "sound_revision": sound,
        }
    request["directive"] = canvas.CanvasDirective("create", {}, connect=True)
    nodes = canvas._scope(task, request, canvas._successful_operations(session, task))
    return {"allowed_nodes": [{"id": key, **value} for key, value in nodes.items()]}
