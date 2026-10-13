"""角色锁内版本读取与追加；事务和锁由 Service 调用者管理。"""

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from short_drama.domain import AssetVersion, AssetVersionMediaReference, MediaFile


class AssetVersionDAO:
    def __init__(self, session: Session):
        self.session = session

    def current(self, asset_id: int, version_id: int) -> AssetVersion | None:
        return self.session.scalar(
            select(AssetVersion)
            .where(AssetVersion.asset_id == asset_id, AssetVersion.id == version_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

    def latest_number(self, asset_id: int) -> int:
        return (
            self.session.scalar(
                select(AssetVersion.version)
                .where(AssetVersion.asset_id == asset_id)
                .order_by(AssetVersion.version.desc())
                .limit(1)
                .with_for_update(read=True)
            )
            or 0
        )

    def references(self, asset_id: int, version_id: int) -> list[AssetVersionMediaReference]:
        return list(
            self.session.scalars(
                select(AssetVersionMediaReference)
                .where(
                    AssetVersionMediaReference.asset_id == asset_id,
                    AssetVersionMediaReference.version_id == version_id,
                )
                .order_by(AssetVersionMediaReference.purpose, AssetVersionMediaReference.ordinal)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )

    def media(self, media_id: int) -> MediaFile | None:
        cached = self.session.identity_map.get(
            self.session.identity_key(MediaFile, ident=(media_id,))
        )
        query = select(MediaFile).where(MediaFile.id == media_id).with_for_update()
        if cached is None or not self.session.is_modified(cached, include_collections=False):
            return self.session.scalar(query.execution_options(populate_existing=True))
        media = self.session.scalar(query)
        if media is not None:
            state = inspect(media)
            unchanged = [
                column.key
                for column in state.mapper.column_attrs
                if not state.attrs[column.key].history.has_changes()
            ]
            # 锁内读取当前列，不覆盖调用者尚待 flush 的媒体修改及其历史。
            if unchanged:
                self.session.refresh(media, attribute_names=unchanged, with_for_update=True)
        return media

    def add(self, entity: AssetVersion | AssetVersionMediaReference) -> None:
        self.session.add(entity)
