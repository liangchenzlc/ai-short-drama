"""Locked shot/video source snapshots and pre-submit freshness checks."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.domain import (
    Asset,
    Project,
    ShotImage,
    ShotScript,
    ShotVideo,
)
from short_drama.domain.agent_media import AgentMediaRequest
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.assistant_media_service import lock_media_project
from short_drama.service.generation_context_service import GenerationContextService
from short_drama.service.image_adoption_state import _target_state
from short_drama.service.native_voice_context import native_context
from short_drama.service.shot_video_context import video_context_hash


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
