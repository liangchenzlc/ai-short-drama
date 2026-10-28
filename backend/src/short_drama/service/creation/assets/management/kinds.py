"""素材类型转换不移除关联、角色历史或声音定义。"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Asset, AssetVersion, CharacterVoice, ShotDialogue


def validate_asset_kind_change(session: Session, asset: Asset, values: Mapping[str, Any]) -> None:
    kind = values.get("kind", asset.kind)
    scene_time = values.get("scene_time", asset.scene_time)
    if kind != "scene" and scene_time:
        raise WorkflowError("invalid_asset", "仅场景可保存场景时间，请先明确修改场景时间", 422)
    if kind == asset.kind:
        return
    if asset.kind == "character":
        if asset.current_version_id is not None or session.scalar(
            select(AssetVersion.asset_id).where(AssetVersion.asset_id == asset.id).limit(1)
        ):
            raise WorkflowError(
                "asset_kind_incompatible", "角色已有不可变作品历史，不能转换为场景或道具", 422
            )
        if session.scalar(
            select(CharacterVoice.asset_id).where(CharacterVoice.asset_id == asset.id).limit(1)
        ):
            raise WorkflowError("asset_kind_incompatible", "角色已有声音绑定，不能修改类型", 422)
        for document in session.scalars(select(ShotDialogue.document)):
            if any(
                str(line.get("character_id")) == str(asset.id) for line in document.get("lines", [])
            ):
                raise WorkflowError(
                    "asset_kind_incompatible", "角色仍被对白引用，不能修改类型", 422
                )
    # ShotAsset 是通用关联，可保留。kind 和取消确认会使旧分镜媒体上下文过期。
