"""Freeze reviewed scope, source versions, quantities and models before execution."""

import hashlib
import json
import re
from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import AIModelConfig, Asset, Project, ShotScript
from short_drama.schemas.agent_runtime import TaskSpec
from short_drama.service.agent_model_service import model_snapshot


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def discussion_only(content):
    # Conservative narrowing. Ambiguous free text gets a reviewed plan, never direct media.
    return bool(
        re.search(
            r"先讨论|只讨论|仅讨论|不要(?:直接)?生成|不(?:要)?执行|别生成|先不生成|"
            r"do not (?:generate|execute)|discussion only",
            content,
            re.I,
        )
    )


def scoped_task(conversation, payload):
    """Bind creative effects to the immutable conversation, never model-picked scope."""
    spec = TaskSpec.model_validate(payload)
    if getattr(conversation, "scope_version", 0) != 1:
        return spec
    subject = conversation.subject_type
    allowed = {
        ("source", "project"): {"novel", "script"},
        ("assets", "project"): {"extract"},
        ("assets", "asset"): {"asset_patch", "image"},
        ("storyboard", "project"): {"storyboard"},
        ("storyboard", "shot"): {"shot_patch", "image", "video"},
    }.get((conversation.stage, subject), set())
    if conversation.task_type == "batch" and subject == "project":
        allowed = (
            {"asset_patch", "image"}
            if conversation.stage == "assets"
            else {"shot_patch", "image", "video"}
        )
    if conversation.task_type in {"image", "video"}:
        allowed &= {conversation.task_type}
    if spec.kind not in allowed:
        raise WorkflowError(
            "agent_scope_mismatch", "该任务不属于当前流程会话，请打开对应对象的会话", 409
        )
    if subject != "project" or spec.kind in {"novel", "script", "extract", "storyboard"}:
        if spec.target_id is not None and spec.target_id != conversation.subject_id:
            raise WorkflowError("agent_scope_mismatch", "该任务对象不属于当前会话", 409)
        spec = spec.model_copy(
            update={
                "target_id": conversation.subject_id,
                "parameters": {**spec.parameters, "target_kind": subject}
                if spec.kind in {"image", "video"}
                else spec.parameters,
            }
        )
    if (
        conversation.stage == "assets"
        and spec.kind == "image"
        and (spec.parameters.get("target_kind") != "asset")
    ):
        raise WorkflowError("agent_scope_mismatch", "素材会话只能生成当前作品素材图片", 409)
    if (
        conversation.stage == "storyboard"
        and spec.kind == "image"
        and (spec.parameters.get("target_kind") == "asset")
    ):
        raise WorkflowError("agent_scope_mismatch", "分镜会话只能生成当前作品镜头图片", 409)
    return spec


def freeze_task(session, conversation, payload, *, step_id, owner_user_id):
    from short_drama.service.agent_conversation_service import validate_conversation_subject

    validate_conversation_subject(session, conversation, lock=True)
    spec = scoped_task(conversation, payload)
    creation = session.scalar(
        select(Project)
        .where(Project.id == conversation.project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if creation is None or creation.id != conversation.project_id:
        raise NotFound("Project does not exist")
    source = {
        "project_id": str(creation.id),
        "content_version": creation.content_version,
        "storyboard_version": creation.storyboard_version,
        "project_row_version": creation.row_version,
    }
    target = spec.target_id
    target_kind = spec.parameters.get("target_kind")
    target_label = f"当前作品：{creation.name}"
    if spec.kind in {"novel", "script", "extract", "storyboard"}:
        if target is not None and target != creation.id:
            raise NotFound("Task target is outside this creation")
        target, target_kind = creation.id, "project"
    else:
        if target is None:
            raise WorkflowError("agent_target_required", "请明确要修改或生成的素材、镜头", 422)
        if spec.kind == "asset_patch" or spec.kind == "image" and target_kind == "asset":
            asset = session.scalar(
                select(Asset)
                .where(
                    Asset.id == target,
                    Asset.project_id == conversation.project_id,
                    Asset.deleted_at.is_(None),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if asset is None:
                raise NotFound("Asset is outside this project")
            source.update(target_row_version=asset.row_version)
            target_kind = "asset"
            target_label = f"素材：{asset.name}"
        else:
            shot = session.scalar(
                select(ShotScript)
                .where(
                    ShotScript.id == target,
                    ShotScript.project_id == creation.id,
                    ShotScript.deleted_at.is_(None),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if shot is None:
                raise NotFound("Shot is outside this creation")
            source.update(target_row_version=shot.row_version)
            target_kind = "shot"
            target_label = f"第 {shot.position} 个镜头：{shot.script[:80]}"
    values = spec.model_dump(mode="json")
    values.update(
        id=step_id,
        target_id=str(target),
        target_kind=target_kind,
        target_label=target_label,
        source=source,
        model_name=None,
    )
    if spec.kind in {"image", "video"}:
        if spec.model_config_id is None:
            raise WorkflowError("agent_media_model_required", "计划需要明确图片或视频模型", 422)
        model = session.scalar(
            select(AIModelConfig)
            .where(
                AIModelConfig.id == spec.model_config_id,
                AIModelConfig.owner_user_id == owner_user_id,
                AIModelConfig.service_type == spec.kind,
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if model is None:
            raise NotFound("Media model does not exist")
        values.update(model_name=model.name, model_snapshot=model_snapshot(model))
    elif spec.model_config_id is not None:
        raise WorkflowError("agent_task_model_unexpected", "文本候选使用本轮决策模型", 422)
    if spec.kind in {"extract", "storyboard", "image", "video"}:
        from short_drama.agent.native_tasks import freeze_native_context

        freeze_native_context(session, values)
    return values


def public_step(step):
    return {key: deepcopy(value) for key, value in step.items() if key != "model_snapshot"}


def check_source(session, step):
    source = step["source"]
    creation = session.scalar(
        select(Project)
        .where(Project.id == int(source["project_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if creation is None or creation.content_version != source["content_version"]:
        raise WorkflowError("agent_source_changed", "正文已变更，请重新核对任务范围", 409)
    if (
        step["kind"] in {"storyboard", "shot_patch", "image", "video"}
        and creation.storyboard_version != source["storyboard_version"]
    ):
        raise WorkflowError("agent_source_changed", "分镜已变更，请重新核对任务范围", 409)
    if step["target_kind"] != "project":
        model = Asset if step["target_kind"] == "asset" else ShotScript
        target = session.scalar(
            select(model)
            .where(model.id == int(step["target_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            target is None
            or target.row_version != source["target_row_version"]
            or getattr(target, "deleted_at", None)
        ):
            raise WorkflowError("agent_source_changed", "任务对象已变更，请重新核对", 409)
