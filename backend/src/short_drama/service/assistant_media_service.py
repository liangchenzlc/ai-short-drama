"""Native standard media admission for authorized assistant targets, without external I/O."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import AIModelConfig, Asset, Project, ShotScript
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.assistant_image_service import image_adoption_state
from short_drama.service.generation_context_service import GenerationContextService


def lock_media_project(session, project_id, owner):
    project = session.scalar(
        select(Project)
        .where(Project.id == int(project_id), Project.owner_user_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or project.archived_at or project.workspace_mode != "standard":
        raise NotFound("图片生成目标作品不存在或不再可访问")
    if project.storyboard_version == UINT64_MAX:
        raise Conflict("作品版本已达到上限，无法回填图片")
    return project


def prepare_image_locked(session, owner, project_id, values, trusted_version):
    project = lock_media_project(session, project_id, owner)
    model = session.scalar(
        select(AIModelConfig)
        .where(AIModelConfig.id == values.model_config_id, AIModelConfig.owner_user_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if model is None or model.service_type != "image" or not model.enabled or model.is_deleted:
        raise WorkflowError("assistant_model_unavailable", "本人图片模型已停用或不可用", 409)
    if str(model.row_version) != str(values.model_version):
        raise Conflict("图片模型配置已变化，请重新核对")
    target = session.scalar(
        select(Asset if values.target_kind == "asset" else ShotScript)
        .where(
            (Asset if values.target_kind == "asset" else ShotScript).id == values.target_id,
            (Asset if values.target_kind == "asset" else ShotScript).project_id == project.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None or target.deleted_at is not None:
        raise NotFound("生成目标已删除或归档")
    if str(values.row_version) != str(trusted_version) or target.row_version != trusted_version:
        raise Conflict("生成目标在选入后已变化，请核对并主动刷新引用")
    if target.row_version == UINT64_MAX:
        raise Conflict("生成目标版本已达到上限，无法回填图片")
    source = {"scene": f"{values.target_kind}_image", f"{values.target_kind}_id": str(target.id)}
    parameters = {"count": values.count, "aspect": values.aspect, "resolution": values.resolution}
    if values.target_kind == "asset":
        source.update(row_version=str(target.row_version), project_id=str(project.id))
    else:
        _project, _shot, _assets, _snapshot, context_hash = GenerationContextService(
            session
        ).locked_shot_context(target.id)
        saved = target.image_settings or {"aspect": None, "resolution": "2K", "layout": "single"}
        if values.aspect != saved["aspect"] or values.resolution != saved["resolution"]:
            raise Conflict("图片参数与已保存分镜设置不一致，请先明确修改设置")
        source.update(
            context_mode="saved",
            row_version=str(target.row_version),
            context_hash=context_hash,
            layout=saved["layout"],
        )
    payload = {
        "project_id": str(project.id),
        "config_id": str(model.id),
        "source": source,
        "input": {"prompt": values.prompt, "reference_media_ids": list(values.reference_media_ids)},
        "parameters": parameters,
    }
    return payload, {
        "project_id": str(project.id),
        "kind": values.target_kind,
        "id": str(target.id),
        "row_version": str(target.row_version),
        "before": image_adoption_state(session, target, values.target_kind),
    }


def admit_media_locked(session, owner, intent, settings):
    actor = session.info.get("actor")
    if actor is not None and actor.user_id != owner:
        raise NotFound("媒体生成账号不匹配")
    original = {
        key: session.info[key] for key in ("actor", "resource_scope") if key in session.info
    }
    session.info["actor"] = actor or ActorContext(
        owner, "", "", True, 0, "", f"assistant-media:{intent.operation_id}"
    )

    def authorized_input(request):
        # Native preparation reads hashes/structure for validation. Only the explicit
        # tool prompt and selected references cross the provider boundary.
        snapshot = request.get("source_snapshot") or {}
        if intent.frozen_target.get("media_role") == "dialogue_audio":
            target = intent.frozen_target
            if (
                request.get("input") != target["provider_input"]
                or request.get("parameters") != target["provider_parameters"]
                or snapshot.get("line_hash") != target["sound_binding"]["line_hash"]
            ):
                raise Conflict("已保存台词或音色与冻结来源不一致，未受理配音")
            request["source_snapshot"] = {
                "line_hash": snapshot["line_hash"],
            }
            request.pop("business_intent", None)
            return request
        native = snapshot.get("native_speech")
        if (
            intent.media_kind == "video"
            and native is not None
            and native != intent.frozen_target.get("native_speech")
        ):
            raise WorkflowError(
                "assistant_native_video_scope", "原生有声视频需先明确核对对白与音色来源", 409
            )
        request["input"] = deepcopy(
            intent.frozen_request.get("input") or intent.frozen_target["provider_input"]
        )
        request["source_snapshot"] = {
            key: value
            for key, value in snapshot.items()
            if key
            in {
                "asset_row_version",
                "asset_content_hash",
                "context_hash",
                "row_version",
                "shot_context_hash",
                "reference_media_id",
                "reference_layout",
                "input_mode",
                "native_speech",
            }
        }
        request.pop("business_intent", None)
        return request

    try:
        summary, _created = AIGenerationService(session, settings).create_locked(
            intent.media_kind,
            intent.frozen_request,
            f"assistant-media:{intent.id}",
            prepare_transform=authorized_input,
            agent_managed=True,
            assistant_managed=True,
        )
        intent.generation_task_id = int(summary["generation_id"])
        intent.status = "accepted"
        session.flush()
        return summary
    finally:
        for key in ("actor", "resource_scope"):
            if key in original:
                session.info[key] = original[key]
            else:
                session.info.pop(key, None)
