"""Saved shot video admission and atomic adoption for private assistant media intents."""

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import (
    AIModelConfig,
    Asset,
    AsyncTask,
    MediaAsset,
    Project,
    ShotImage,
    ShotScript,
    ShotVideo,
)
from short_drama.domain.agent_media import AgentMediaRequest
from short_drama.schemas.assistant_media import AssistantShotVideoRequest
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.assistant_image_service import _target_state
from short_drama.service.assistant_media_service import lock_media_project
from short_drama.service.generation_context_service import GenerationContextService
from short_drama.service.media_asset_service import MediaAssetService
from short_drama.service.native_voice_service import native_context, native_prompt
from short_drama.service.shot_video_context import DEFAULT_VIDEO_SETTINGS, video_context_hash


def _video_state(session: Session, shot: ShotScript) -> dict[str, Any]:
    state = _target_state(session, shot, "shot")
    video = session.scalar(select(ShotVideo).where(ShotVideo.shot_id == shot.id).with_for_update())
    state["video"] = (
        {
            key: str(value) if (key == "id" or key.endswith("_id")) and value is not None else value
            for key in (
                "id",
                "media_id",
                "model_id",
                "prompt",
                "resolution",
                "duration",
                "context_hash",
                "first_frame_media_id",
            )
            for value in (getattr(video, key),)
        }
        if video
        else None
    )
    return state


def _current(
    session: Session,
    owner: int,
    project_id: str,
    shot_id: str,
    *,
    settings: Settings | None = None,
) -> tuple[Project, ShotScript, ShotImage, str, dict[str, Any] | None]:
    project = lock_media_project(session, project_id, owner)
    shot = session.scalar(
        select(ShotScript)
        .where(
            ShotScript.id == int(shot_id),
            ShotScript.project_id == project.id,
            ShotScript.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if shot is None:
        raise NotFound("视频目标分镜已删除或不再可访问")
    if shot.row_version == UINT64_MAX:
        raise Conflict("分镜版本已达到上限，无法回填视频")
    _project, _shot, _assets, _snapshot, image_hash = GenerationContextService(
        session
    ).locked_shot_context(shot.id)
    image = session.scalar(select(ShotImage).where(ShotImage.shot_id == shot.id).with_for_update())
    if image is None:
        raise WorkflowError("video_reference_required", "请先采用并主动选入分镜当前参考图", 422)
    native = native_context(session, shot, settings=settings)
    context = video_context_hash(
        image_hash,
        image.media_id,
        shot.video_prompt,
        shot.video_settings,
        session=session,
        shot=shot,
        native_speech=native,
    )
    return project, shot, image, context, native


def _native_source(
    session: Session,
    shot: ShotScript,
    values: AssistantShotVideoRequest,
    trusted: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    if not (settings.native_video_enabled and settings.audio_production_enabled):
        raise WorkflowError("assistant_native_video_disabled", "原生有声视频功能尚未启用", 409)
    current = native_context(session, shot, strict=True, settings=settings)
    if current is None:
        raise Conflict("作品未使用原生有声模式，请先明确保存声音模式")
    dialogue = trusted["dialogue"]
    if (
        str(values.native_dialogue_version) != dialogue["row_version"]
        or str(current["dialogue_version"]) != dialogue["row_version"]
        or {"reviewed": current["reviewed"], "lines": current["lines"]} != dialogue["document"]
    ):
        raise Conflict("分镜对白在选入后已变化，请核对并主动刷新引用")
    supplied = {
        str(voice.character_id): {
            "row_version": str(voice.row_version),
            "voice_version": str(voice.voice_version),
        }
        for voice in values.native_voice_versions
    }
    expected = {
        identifier: {
            "row_version": source["row_version"],
            "voice_version": source["voice_binding"]["row_version"],
        }
        for identifier, source in trusted["voices"].items()
    }
    if supplied != expected or {voice["character_id"] for voice in current["voices"]} != set(
        expected
    ):
        raise Conflict("说话角色或音色版本超出可信来源，请核对角色引用")
    for voice in current["voices"]:
        source = trusted["voices"][voice["character_id"]]
        character = session.scalar(
            select(Asset)
            .where(Asset.id == int(voice["character_id"]), Asset.project_id == shot.project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        binding = {
            "row_version": str(voice["version"]),
            "record_id": voice["record_id"],
            "media_id": voice["media_id"],
        }
        if (
            character is None
            or str(character.row_version) != source["row_version"]
            or binding != source["voice_binding"]
        ):
            raise Conflict("角色或音色在选入后已变化，请核对并主动刷新引用")
    return current


def prepare_shot_video_locked(
    session: Session,
    owner: int,
    project_id: str,
    values: AssistantShotVideoRequest,
    trusted_version: int,
    *,
    trusted_native: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project, shot, image, context, native = _current(
        session, owner, project_id, str(values.target_id), settings=settings
    )
    if values.row_version != trusted_version or shot.row_version != trusted_version:
        raise Conflict("分镜在选入后已变化，请核对并主动刷新引用")
    if values.reference_media_ids != [image.media_id]:
        raise Conflict("视频参考图必须是已明确授权的当前分镜主图")
    if trusted_native is not None:
        native = _native_source(session, shot, values, trusted_native, settings or Settings())
    elif native is not None or values.native_dialogue_version is not None:
        raise WorkflowError(
            "assistant_native_video_scope", "原生有声视频需先明确核对对白与音色来源", 409
        )
    model = session.scalar(
        select(AIModelConfig)
        .where(AIModelConfig.id == values.model_config_id, AIModelConfig.owner_user_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if model is None or model.service_type != "video" or not model.enabled or model.is_deleted:
        raise WorkflowError("assistant_model_unavailable", "本人视频模型已停用或不可用", 409)
    if model.row_version != values.model_version:
        raise Conflict("视频模型配置已变化，请重新核对")
    saved = shot.video_settings or DEFAULT_VIDEO_SETTINGS
    before = _video_state(session, shot)
    return {
        "project_id": str(project.id),
        "config_id": str(model.id),
        "source": {
            "scene": "shot_video",
            "shot_id": str(shot.id),
            "row_version": str(shot.row_version),
            "context_hash": context,
            "reference_media_id": str(image.media_id),
        },
        "parameters": {
            "duration_ms": saved.get("duration_ms") or shot.duration_ms,
            "resolution": saved["resolution"],
        },
    }, {
        "project_id": str(project.id),
        "kind": "shot",
        "id": str(shot.id),
        "name": shot.title or f"分镜 {shot.position} 视频",
        "row_version": str(shot.row_version),
        "before": before,
        "context_hash": context,
        "reference_media_id": str(image.media_id),
        "reference_layout": image.layout,
        **(
            {"native_speech": native, "native_sources": deepcopy(trusted_native)}
            if trusted_native is not None
            else {}
        ),
        "provider_input": {
            "prompt": values.prompt + (native_prompt(native) if trusted_native is not None else ""),
            "reference_media_ids": list(values.reference_media_ids),
            **(
                {"audio_reference_media_ids": [voice["media_id"] for voice in native["voices"]]}
                if trusted_native is not None
                else {}
            ),
        },
    }


def shot_video_may_submit(
    session: Session,
    owner: int,
    intent: AgentMediaRequest,
    *,
    settings: Settings | None = None,
) -> bool:
    target = intent.frozen_target
    try:
        _project, shot, image, context, native = _current(
            session, owner, target["project_id"], target["id"], settings=settings
        )
        if target.get("native_speech") is not None:
            runtime = settings or Settings()
            if not (runtime.native_video_enabled and runtime.audio_production_enabled):
                return False
            if native != target["native_speech"]:
                return False
            for identifier, source in target["native_sources"]["voices"].items():
                character = session.get(Asset, int(identifier), populate_existing=True)
                if character is None or str(character.row_version) != source["row_version"]:
                    return False
    except BusinessError:
        return False
    current = _video_state(session, shot)
    return (
        str(shot.row_version) == target["row_version"]
        and context == target["context_hash"]
        and str(image.media_id) == target["reference_media_id"]
        and image.layout == target["reference_layout"]
        and current["video"] == target["before"]["video"]
    )


def settle_shot_video_locked(
    session: Session,
    owner: int,
    intent: AgentMediaRequest,
    native: AsyncTask,
    outputs: list[MediaAsset],
    *,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], str, dict[str, str] | None]:
    target = intent.frozen_target
    available = (
        session.scalar(
            select(ShotScript.id)
            .join(Project, Project.id == ShotScript.project_id)
            .where(
                ShotScript.id == int(target["id"]),
                ShotScript.project_id == int(target["project_id"]),
                ShotScript.deleted_at.is_(None),
                Project.owner_user_id == owner,
                Project.archived_at.is_(None),
                Project.workspace_mode == "standard",
            )
        )
        is not None
    )
    result = {
        "adopted": False,
        "target_version": target["row_version"],
        "target_available": available,
        "items": [
            {
                "id": str(output.id),
                "media_id": str(output.media_id),
                "output_index": output.output_index,
                "status": "ready",
            }
            for output in outputs
        ],
    }
    if native.status != "succeeded" or len(outputs) != 1:
        return (
            result,
            "failed",
            {
                "code": "assistant_media_partial_failure",
                "message": "视频未全部生成成功，已保存产物保留在私人任务和素材中",
            },
        )
    if not available or not shot_video_may_submit(session, owner, intent, settings=settings):
        return (
            result,
            "conflict",
            {
                "code": "assistant_media_adoption_conflict",
                "message": "分镜、参考图或当前视频已变化，产物已保留",
            },
        )
    original = {
        key: session.info[key] for key in ("actor", "resource_scope") if key in session.info
    }
    actor = session.info.get("actor")
    if actor is not None and actor.user_id != owner:
        raise NotFound("视频采用账号不匹配")
    session.info["actor"] = actor or ActorContext(
        owner, "", "", True, 0, "", f"assistant-media:{intent.operation_id}"
    )
    try:
        try:
            with session.begin_nested():
                project = session.get(Project, int(target["project_id"]))
                storyboard_before = str(project.storyboard_version)
                receipt = MediaAssetService(session, None, None).apply_locked(
                    outputs[0].id,
                    {
                        "target": {"type": "shot_video", "id": target["id"]},
                        "expected_row_version": target["row_version"],
                        "expected_context_hash": target["context_hash"],
                        "expected_media_id": (target["before"]["video"] or {}).get("media_id"),
                    },
                )
        except BusinessError as error:
            return result, "conflict", {"code": error.code, "message": error.message}
        return (
            {
                **result,
                "adopted": True,
                "target_version": receipt["row_version"],
                "storyboard_transition": {
                    "before": storyboard_before,
                    "after": str(project.storyboard_version),
                },
            },
            "succeeded",
            None,
        )
    finally:
        for key in ("actor", "resource_scope"):
            if key in original:
                session.info[key] = original[key]
            else:
                session.info.pop(key, None)


def shot_video_changes_locked(session: Session, intent: AgentMediaRequest) -> list[dict[str, Any]]:
    if not intent.result or not intent.result.get("adopted"):
        return []
    target = intent.frozen_target
    shot = session.get(ShotScript, int(target["id"]))
    if shot is None or str(shot.row_version) != intent.result["target_version"]:
        return []
    after = _video_state(session, shot)
    if (after["video"] or {}).get("media_id") != intent.result["items"][0]["media_id"]:
        return []
    return [
        {
            "kind": "shot",
            "id": target["id"],
            "media_role": "video",
            "before": deepcopy(target["before"]),
            "after": after,
        }
    ]
