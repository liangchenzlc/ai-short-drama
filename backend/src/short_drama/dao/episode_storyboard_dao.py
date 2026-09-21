"""Locked persistence primitives for the episode storyboard aggregate."""

from sqlalchemy import case, delete, func, select

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain.asset import Asset
from short_drama.domain.episode import Episode
from short_drama.domain.episode_asset import EpisodeAsset
from short_drama.domain.media_asset import MediaAsset
from short_drama.domain.media_file import MediaFile
from short_drama.domain.shot_asset import ShotAsset
from short_drama.domain.shot_image import ShotImage
from short_drama.domain.shot_script import ShotScript
from short_drama.schemas.base import UINT64_MAX

from .base import BaseDAO


def require_storyboard_version(episode: Episode, expected: int) -> None:
    if episode.storyboard_version != expected:
        raise WorkflowError(
            "storyboard_version_conflict",
            "分镜列表已变化，请刷新后重试",
            details={"current_version": episode.storyboard_version},
        )


def require_shot_version(shot: ShotScript, expected: int) -> None:
    if shot.row_version != expected:
        raise WorkflowError(
            "shot_version_conflict",
            "分镜内容已变化，请刷新后重试",
            details={"current_version": shot.row_version},
        )


def advance_storyboard_version(episode: Episode) -> int:
    if episode.storyboard_version >= UINT64_MAX:
        raise Conflict("Storyboard version exhausted")
    episode.storyboard_version += 1
    return episode.storyboard_version


def advance_shot_version(shot: ShotScript) -> int:
    if shot.row_version >= UINT64_MAX:
        raise Conflict("Shot version exhausted")
    shot.row_version += 1
    return shot.row_version


class EpisodeStoryboardDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, ShotScript)

    def scoped_episode(self, project_id: int, episode_id: int) -> Episode:
        episode = self.session.scalar(
            select(Episode)
            .where(Episode.id == episode_id, Episode.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if episode is None:
            raise WorkflowError("not_found", "分集不存在或不属于当前项目", 404)
        return episode

    def scoped_shot(self, episode_id: int, shot_id: int, *, for_update=True) -> ShotScript:
        statement = select(ShotScript).where(
            ShotScript.id == shot_id, ShotScript.episode_id == episode_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        shot = self.session.scalar(statement)
        if shot is None:
            raise WorkflowError("not_found", "分镜不存在或不属于当前分集", 404)
        return shot

    def by_creation_key(self, creation_key: str) -> ShotScript | None:
        return self.session.scalar(
            select(ShotScript)
            .where(ShotScript.creation_key == creation_key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def list_rows(self, episode_id: int, include_archived: bool, offset: int, limit: int):
        conditions = [ShotScript.episode_id == episode_id]
        if not include_archived:
            conditions.append(ShotScript.deleted_at.is_(None))
        ordering = [
            case((ShotScript.deleted_at.is_(None), 0), else_=1),
            ShotScript.position,
            ShotScript.id,
        ]
        rows = list(
            self.session.scalars(
                select(ShotScript)
                .where(*conditions)
                .order_by(*ordering)
                .offset(offset)
                .limit(limit)
            )
        )
        total = self.session.scalar(select(func.count()).select_from(ShotScript).where(*conditions))
        return rows, total

    def list_active_for_update(self, episode_id: int) -> list[ShotScript]:
        return list(
            self.session.scalars(
                select(ShotScript)
                .where(
                    ShotScript.episode_id == episode_id,
                    ShotScript.deleted_at.is_(None),
                )
                .order_by(ShotScript.position, ShotScript.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def assets(self, shot_id: int) -> list[Asset]:
        return list(
            self.session.scalars(
                select(Asset)
                .join(ShotAsset, ShotAsset.asset_id == Asset.id)
                .where(ShotAsset.shot_id == shot_id)
                .order_by(Asset.id)
            )
        )

    def asset_ids(self, shot_id: int) -> list[int]:
        return list(
            self.session.scalars(
                select(ShotAsset.asset_id)
                .where(ShotAsset.shot_id == shot_id)
                .order_by(ShotAsset.asset_id)
            )
        )

    def require_episode_assets(self, episode_id: int, asset_ids: list[int]) -> None:
        if not asset_ids:
            return
        found = set(
            self.session.scalars(
                select(EpisodeAsset.asset_id).where(
                    EpisodeAsset.episode_id == episode_id,
                    EpisodeAsset.asset_id.in_(asset_ids),
                )
            )
        )
        if found != set(asset_ids):
            raise WorkflowError("not_found", "一个或多个素材不属于当前分集素材库", 404)

    def replace_assets(self, shot: ShotScript, asset_ids: list[int], now) -> None:
        self.session.execute(delete(ShotAsset).where(ShotAsset.shot_id == shot.id))
        links = BaseDAO(self.session, ShotAsset)
        for asset_id in sorted(asset_ids):
            links.create(
                {
                    "episode_id": shot.episode_id,
                    "shot_id": shot.id,
                    "asset_id": asset_id,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": None,
                    "updated_by": None,
                }
            )

    def image_row(self, shot_id: int):
        return self.session.execute(
            select(ShotImage, MediaFile, MediaAsset.id)
            .join(MediaFile, MediaFile.id == ShotImage.media_id)
            .outerjoin(MediaAsset, MediaAsset.media_id == MediaFile.id)
            .where(ShotImage.shot_id == shot_id)
        ).first()
