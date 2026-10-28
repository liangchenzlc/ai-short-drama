"""Frozen character voice requests and atomic adoption through the native voice service."""

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    Asset,
    AsyncTask,
    MediaAsset,
    Project,
)
from short_drama.domain.assistant.operations.media import AgentMediaRequest
from short_drama.domain.creation.sound.voices.voices import CharacterVoice
from short_drama.schemas.assistant.operations.media import AssistantCharacterVoiceRequest
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.creation.sound.voices.voices import NativeVoiceService


def voice_binding_state(
    session: Session, project_id: int | str, asset_id: int | str, *, lock: bool = False
) -> dict[str, Any]:
    statement = select(CharacterVoice).where(
        CharacterVoice.project_id == int(project_id), CharacterVoice.asset_id == int(asset_id)
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(statement)
    return {
        "row_version": str(row.row_version) if row else "0",
        "record_id": str(row.record_id) if row and row.record_id is not None else None,
        "media_id": str(row.media_id) if row and row.media_id is not None else None,
    }


def _current(
    session: Session,
    owner: int,
    project_id: int | str,
    asset_id: int | str,
    *,
    settings: Settings | None = None,
    check_flags: bool = True,
) -> tuple[Asset, dict[str, Any]]:
    if check_flags:
        settings = settings or Settings()
        if not (
            getattr(settings, "native_video_enabled", False)
            and getattr(settings, "audio_production_enabled", False)
        ):
            raise WorkflowError("assistant_voice_disabled", "角色声音功能尚未启用", 409)
    project = session.scalar(
        select(Project)
        .where(
            Project.id == int(project_id),
            Project.owner_user_id == owner,
            Project.workspace_mode == "standard",
            Project.archived_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise NotFound("音色目标作品不存在或不再可访问")
    asset = session.scalar(
        select(Asset)
        .where(
            Asset.id == int(asset_id),
            Asset.project_id == project.id,
            Asset.kind == "character",
            Asset.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if asset is None:
        raise NotFound("音色目标角色已删除或不再可访问")
    return asset, voice_binding_state(session, project.id, asset.id, lock=True)


def prepare_voice_design_locked(
    session: Session,
    owner: int,
    project_id: str,
    values: AssistantCharacterVoiceRequest,
    trusted_version: int,
    *,
    trusted_voice: dict[str, Any] | None,
    settings: Settings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    asset, binding = _current(session, owner, project_id, values.target_id, settings=settings)
    if values.row_version != trusted_version or asset.row_version != trusted_version:
        raise Conflict("角色在选入后已变化，请核对并主动刷新引用")
    if (
        trusted_voice is None
        or str(values.voice_version) != trusted_voice["row_version"]
        or binding != trusted_voice
    ):
        raise Conflict("角色音色在选入后已变化或缺少可信版本，请核对并主动刷新引用")
    if int(binding["row_version"]) == UINT64_MAX:
        raise Conflict("角色音色版本已达到上限，无法采用新音色")
    model = session.scalar(
        select(AIModelConfig)
        .where(AIModelConfig.id == values.model_config_id, AIModelConfig.owner_user_id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if model is None or model.service_type != "audio" or not model.enabled or model.is_deleted:
        raise WorkflowError("assistant_model_unavailable", "本人音频模型已停用或不可用", 409)
    if model.row_version != values.model_version:
        raise Conflict("音频模型配置已变化，请重新核对")
    return {
        "project_id": str(project_id),
        "config_id": str(model.id),
        "source": {
            "scene": "character_voice_design",
            "project_id": str(project_id),
            "asset_id": str(asset.id),
            "voice_prompt": values.prompt,
            "preview_text": values.preview_text,
        },
    }, {
        "project_id": str(project_id),
        "kind": "asset",
        "id": str(asset.id),
        "name": f"{asset.name} · 角色音色",
        "media_role": "character_voice",
        "row_version": str(asset.row_version),
        "voice_binding": binding,
        "provider_input": {"voice_prompt": values.prompt, "preview_text": values.preview_text},
    }


def voice_design_may_submit(
    session: Session,
    owner: int,
    intent: AgentMediaRequest,
    *,
    settings: Settings | None = None,
    check_flags: bool = True,
) -> bool:
    target = intent.frozen_target
    try:
        asset, binding = _current(
            session,
            owner,
            target["project_id"],
            target["id"],
            settings=settings,
            check_flags=check_flags,
        )
    except BusinessError:
        return False
    return str(asset.row_version) == target["row_version"] and binding == target["voice_binding"]


def settle_voice_design_locked(
    session: Session,
    owner: int,
    intent: AgentMediaRequest,
    native: AsyncTask,
    record: AIGenerationRecord,
    outputs: list[MediaAsset],
    settings: Settings,
) -> tuple[dict[str, Any], str, dict[str, str] | None]:
    target = intent.frozen_target
    try:
        _current(session, owner, target["project_id"], target["id"], check_flags=False)
        available = True
    except BusinessError:
        available = False
    result = {
        "adopted": False,
        "target_available": available,
        "target_version": target["row_version"],
        "voice_version": target["voice_binding"]["row_version"],
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
                "message": "音色未生成成功，已保存样音保留在私人任务和素材中",
            },
        )
    if not voice_design_may_submit(session, owner, intent, check_flags=False):
        return (
            result,
            "conflict",
            {
                "code": "assistant_media_adoption_conflict",
                "message": "角色或当前音色已变化，生成样音已保留",
            },
        )
    original = {
        key: session.info[key] for key in ("actor", "resource_scope") if key in session.info
    }
    actor = session.info.get("actor")
    if actor is not None and actor.user_id != owner:
        raise NotFound("音色采用账号不匹配")
    session.info["actor"] = actor or ActorContext(
        owner, "", "", True, 0, "", f"assistant-media:{intent.operation_id}"
    )
    try:
        try:
            with session.begin_nested():
                adopted = NativeVoiceService(session, settings).adopt_locked(
                    target["project_id"],
                    target["id"],
                    {"row_version": int(result["voice_version"]), "record_id": str(record.id)},
                )
        except BusinessError as error:
            return result, "conflict", {"code": error.code, "message": error.message}
        result.update(
            adopted=True,
            voice_version=str(adopted["row_version"]),
            voice_binding=voice_binding_state(session, target["project_id"], target["id"]),
        )
        return result, "succeeded", None
    finally:
        for key in ("actor", "resource_scope"):
            if key in original:
                session.info[key] = original[key]
            else:
                session.info.pop(key, None)


def voice_design_changes_locked(
    session: Session, intent: AgentMediaRequest
) -> list[dict[str, Any]]:
    if not (intent.result or {}).get("adopted"):
        return []
    target = intent.frozen_target
    current = voice_binding_state(session, target["project_id"], target["id"], lock=True)
    if current != intent.result["voice_binding"]:
        return []
    return [
        {
            "kind": "character_voice",
            "id": target["id"],
            "before": deepcopy(target["voice_binding"]),
            "after": current,
        }
    ]
