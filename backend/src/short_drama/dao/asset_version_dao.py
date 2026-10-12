"""角色锁内版本读取与追加；事务和锁由 Service 调用者管理。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import AssetVersion, AssetVersionMediaReference


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

    def add(self, entity: AssetVersion | AssetVersionMediaReference) -> None:
        self.session.add(entity)
