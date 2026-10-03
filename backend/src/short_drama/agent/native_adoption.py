"""Shared native candidates use the original review and optimistic adoption rules."""

from pydantic import ValidationError
from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    MediaAsset,
    ShotImage,
    ShotScript,
    ShotVideo,
)
from short_drama.schemas.asset_extraction import ExtractionApply
from short_drama.schemas.media_asset import MediaAssetApply
from short_drama.schemas.storyboard_apply import StoryboardApply
from short_drama.service.asset_extraction_service import AssetExtractionService
from short_drama.service.generation_business_service import GenerationBusinessService
from short_drama.service.media_asset_service import MediaAssetService


def _review(schema, values):
    try:
        return schema.model_validate(values)
    except ValidationError:
        raise WorkflowError(
            "invalid_agent_native_review", "审核参数不完整或不受支持", 422
        ) from None


def adopt_native_locked(session, episode, artifact, values, *, settings=None, storage=None):
    if artifact.kind == "extraction_candidate":
        if values.get("native_review") is None:
            raise WorkflowError(
                "agent_native_review_required", "请核对提取候选的创建或复用方式", 409
            )
        review = _review(ExtractionApply, values["native_review"])
        if review.content_version != values["content_version"]:
            raise WorkflowError("agent_source_changed", "审核的正文版本不一致", 409)
        receipt = AssetExtractionService(session).apply_locked(
            episode.project_id,
            episode.id,
            artifact.generation_task_id,
            review.model_dump(mode="json"),
            f"agent-artifact:{artifact.id}",
        )
        return {**receipt, "action": "apply_extraction", "changed": not receipt["already_applied"]}
    if artifact.kind == "storyboard_candidate":
        if values.get("native_review") is None:
            raise WorkflowError("agent_native_review_required", "请选择分镜追加或替换方式", 409)
        review = _review(StoryboardApply, values["native_review"])
        if review.content_version != values[
            "content_version"
        ] or review.storyboard_version != values.get("storyboard_version"):
            raise WorkflowError("agent_source_changed", "审核的正文或分镜版本不一致", 409)
        receipt = GenerationBusinessService(session).apply_storyboard_locked(
            episode.project_id,
            episode.id,
            artifact.generation_task_id,
            review.model_dump(mode="json"),
        )
        return {**receipt, "action": "apply_storyboard", "changed": not receipt["already_applied"]}
    if values.get("native_review") is not None:
        raise WorkflowError("invalid_agent_native_review", "媒体候选不接受文本审核参数", 422)
    source = artifact.source_snapshot
    if artifact.media_asset_id is None:
        raise WorkflowError("agent_artifact_not_ready", "媒体尚未保存完成", 409)
    media = session.scalar(select(MediaAsset).where(MediaAsset.id == artifact.media_asset_id))
    record = (
        session.scalar(select(AIGenerationRecord).where(AIGenerationRecord.id == media.record_id))
        if media
        else None
    )
    if media is None or record is None or record.task_id != artifact.generation_task_id:
        raise NotFound("Candidate media does not exist")
    if artifact.target_asset_id:
        target = session.scalar(
            select(Asset)
            .where(Asset.id == artifact.target_asset_id, Asset.project_id == episode.project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        slot, target_type = target, "asset_image"
        expected_media = target.media_id if target else None
        context_hash = None
    else:
        target = session.scalar(
            select(ShotScript)
            .where(
                ShotScript.id == artifact.target_shot_id,
                ShotScript.episode_id == episode.id,
                ShotScript.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        slot_model = ShotVideo if artifact.kind == "video_candidate" else ShotImage
        slot = session.scalar(
            select(slot_model)
            .where(slot_model.shot_id == artifact.target_shot_id)
            .with_for_update()
        )
        expected_media = slot.media_id if slot else None
        target_type = "shot_video" if artifact.kind == "video_candidate" else "shot_image"
        context_hash = (record.request_data.get("source_snapshot") or {}).get("context_hash")
        if (
            episode.storyboard_version != source["storyboard_version"]
            or values.get("storyboard_version") != episode.storyboard_version
        ):
            raise WorkflowError("agent_source_changed", "分镜集合已变化，请核对新的来源", 409)
    if target is None:
        raise NotFound("Candidate target does not exist")
    if (
        target.row_version != source["target_row_version"]
        or values.get("target_row_version") != target.row_version
    ):
        raise WorkflowError("agent_source_changed", "候选对象已变化，请核对后重新生成", 409)
    if values.get("confirm_shared") is not True:
        raise WorkflowError(
            "shared_confirmation_required", "采用媒体会更新共享作品，请明确确认", 409
        )
    before = target.row_version
    parsed = MediaAssetApply.model_validate(
        {
            "target": {"type": target_type, "id": target.id},
            "expected_media_id": expected_media,
            "expected_row_version": target.row_version,
            "expected_context_hash": context_hash,
            "confirm_shared": True,
        }
    )
    result = MediaAssetService(session, settings or Settings(), storage).apply_locked(
        media.id, parsed.model_dump()
    )
    return {
        **result,
        "action": "adopt_media",
        "target_row_version": target.row_version,
        "asset_id" if artifact.target_asset_id else "shot_id": str(target.id),
        "changed": target.row_version != before,
    }
