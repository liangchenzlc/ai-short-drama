"""角色目录只查询实际关系与实体，沿用各实体的 Actor 查询限制。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import Asset, MediaFile, ProjectAsset
from short_drama.domain.native_voice import CharacterVoice

from .asset_version_dao import AssetVersionDAO


class CanvasCharacterCatalogDAO:
    def __init__(self, session: Session):
        self.session = session

    def relations(self, source_project_id: int) -> list[ProjectAsset]:
        return list(
            self.session.scalars(
                select(ProjectAsset)
                .where(ProjectAsset.project_id == source_project_id)
                .order_by(ProjectAsset.position, ProjectAsset.id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )

    def relation(self, source_project_id: int, asset_id: int) -> ProjectAsset | None:
        return self.session.scalar(
            select(ProjectAsset)
            .where(ProjectAsset.project_id == source_project_id, ProjectAsset.asset_id == asset_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

    def locked_asset(self, asset_id: int) -> Asset | None:
        return self.session.scalar(
            select(Asset)
            .where(Asset.id == asset_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def media(self, media_id: int) -> MediaFile | None:
        return AssetVersionDAO(self.session).media(media_id)

    def voice_binding(self, project_id: int, asset_id: int) -> CharacterVoice | None:
        return self.session.scalar(
            select(CharacterVoice)
            .where(CharacterVoice.project_id == project_id, CharacterVoice.asset_id == asset_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
