"""Locked persistence primitives for the creation storyboard aggregate."""

from sqlalchemy import delete, func, select

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain.asset import Asset
from short_drama.domain.media_asset import MediaAsset
from short_drama.domain.media_file import MediaFile
from short_drama.domain.project import Project
from short_drama.domain.shot_asset import ShotAsset
from short_drama.domain.shot_image import ShotImage
from short_drama.domain.shot_script import ShotScript
from short_drama.domain.shot_video import ShotVideo
from short_drama.schemas.base import UINT64_MAX

from .base import BaseDAO


def require_storyboard_version(creation: Project, expected: int) -> None:
    if creation.storyboard_version != expected:
        raise WorkflowError(
            "storyboard_version_conflict",
            "分镜列表已变化，请刷新后重试",
            details={"current_version": creation.storyboard_version},
        )


def require_shot_version(shot: ShotScript, expected: int) -> None:
    if shot.row_version != expected:
        raise WorkflowError(
            "shot_version_conflict",
            "分镜内容已变化，请刷新后重试",
            details={"current_version": shot.row_version},
        )


def advance_storyboard_version(creation: Project) -> int:
    if creation.storyboard_version >= UINT64_MAX:
        raise Conflict("Storyboard version exhausted")
    creation.storyboard_version += 1
    return creation.storyboard_version


def advance_shot_version(shot: ShotScript) -> int:
    if shot.row_version >= UINT64_MAX:
        raise Conflict("Shot version exhausted")
    shot.row_version += 1
    return shot.row_version


class ProjectStoryboardDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, ShotScript)

    def scoped_project(self, project_id: int, *, for_update=True) -> Project:
        statement = (
            select(Project)
            .where(Project.id == project_id, Project.workspace_mode == "standard")
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        creation = self.session.scalar(statement)
        if creation is None:
            raise WorkflowError("not_found", "作品不存在", 404)
        return creation

    def scoped_shot(self, project_id: int, shot_id: int, *, for_update=True) -> ShotScript:
        statement = select(ShotScript).where(
            ShotScript.id == shot_id, ShotScript.project_id == project_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        shot = self.session.scalar(statement)
        if shot is None:
            raise WorkflowError("not_found", "分镜不存在或不属于当前作品", 404)
        return shot

    def by_creation_key(self, creation_key: str) -> ShotScript | None:
        return self.session.scalar(
            select(ShotScript)
            .where(ShotScript.creation_key == creation_key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def list_rows(self, project_id: int, offset: int, limit: int):
        conditions = [ShotScript.project_id == project_id, ShotScript.deleted_at.is_(None)]
        ordering = [ShotScript.position, ShotScript.id]
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

    def list_active_for_update(self, project_id: int) -> list[ShotScript]:
        return list(
            self.session.scalars(
                select(ShotScript)
                .where(
                    ShotScript.project_id == project_id,
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

    def require_project_assets(self, project_id: int, asset_ids: list[int]) -> None:
        if not asset_ids:
            return
        found = set(
            self.session.scalars(
                select(Asset.id).where(
                    Asset.project_id == project_id,
                    Asset.id.in_(asset_ids),
                    Asset.deleted_at.is_(None),
                )
            )
        )
        if found != set(asset_ids):
            raise WorkflowError("not_found", "一个或多个素材不属于当前作品", 404)

    def replace_assets(self, shot: ShotScript, asset_ids: list[int], now) -> None:
        self.session.execute(delete(ShotAsset).where(ShotAsset.shot_id == shot.id))
        links = BaseDAO(self.session, ShotAsset)
        for asset_id in sorted(asset_ids):
            links.create(
                {
                    "project_id": shot.project_id,
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

    def video_rows(self, shot_ids):
        return (
            {
                video.shot_id: (video, media, asset_id)
                for video, media, asset_id in self.session.execute(
                    select(ShotVideo, MediaFile, MediaAsset.id)
                    .join(MediaFile, MediaFile.id == ShotVideo.media_id)
                    .outerjoin(MediaAsset, MediaAsset.media_id == MediaFile.id)
                    .where(ShotVideo.shot_id.in_(shot_ids))
                )
            }
            if shot_ids
            else {}
        )

    def list_details(self, shot_ids: list[int]):
        assets = {shot_id: [] for shot_id in shot_ids}
        images = {}
        if not shot_ids:
            return assets, images
        for shot_id, asset in self.session.execute(
            select(ShotAsset.shot_id, Asset)
            .join(Asset, Asset.id == ShotAsset.asset_id)
            .where(ShotAsset.shot_id.in_(shot_ids))
            .order_by(ShotAsset.shot_id, Asset.id)
        ):
            assets[shot_id].append(asset)
        for shot_id, image, media, media_asset_id in self.session.execute(
            select(ShotImage.shot_id, ShotImage, MediaFile, MediaAsset.id)
            .join(MediaFile, MediaFile.id == ShotImage.media_id)
            .outerjoin(MediaAsset, MediaAsset.media_id == MediaFile.id)
            .where(ShotImage.shot_id.in_(shot_ids))
        ):
            images[shot_id] = (image, media, media_asset_id)
        return assets, images
