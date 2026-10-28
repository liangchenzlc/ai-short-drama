from sqlalchemy import String, cast, func, or_, select

from short_drama.domain import Asset, MediaFile, Project, ShotAsset, ShotScript


class AssetLibraryDAO:
    """Query assets owned directly by one creation."""

    def __init__(self, session):
        self.session = session

    def scope_conditions(self, kind, parent_id):
        if kind != "project":
            raise ValueError("Only project assets are supported")
        return [Asset.project_id == int(parent_id), Asset.deleted_at.is_(None)]

    def link(self, kind, parent_id, asset_id, *, for_update=False):
        statement = select(Asset).where(
            *self.scope_conditions(kind, parent_id), Asset.id == asset_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def links(self, kind, parent_id, *, asset_kind=None, query="", offset=0, limit=20):
        conditions = self.scope_conditions(kind, parent_id)
        if asset_kind:
            conditions.append(Asset.kind == asset_kind)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            conditions.append(
                or_(
                    Asset.name.like(pattern, escape="\\"),
                    Asset.description.like(pattern, escape="\\"),
                    Asset.label.like(pattern, escape="\\"),
                    cast(Asset.tags, String).like(pattern, escape="\\"),
                )
            )
        statement = (
            select(Asset, MediaFile)
            .outerjoin(MediaFile, MediaFile.id == Asset.media_id)
            .where(*conditions)
        )
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(
            statement.order_by(Asset.position, Asset.id).offset(offset).limit(limit)
        ).all()
        return [(asset, asset, media) for asset, media in rows], total

    def next_position(self, kind, parent_id):
        self.scope_conditions(kind, parent_id)
        value = self.session.scalar(
            select(func.max(Asset.position)).where(Asset.project_id == int(parent_id))
        )
        return (value or 0) + 1

    def reference_count(self, asset_id):
        return self.reference_counts([asset_id])[asset_id]

    def reference_counts(self, asset_ids):
        counts = {asset_id: 0 for asset_id in asset_ids}
        if counts:
            for asset_id, count in self.session.execute(
                select(ShotAsset.asset_id, func.count())
                .join(ShotScript, ShotScript.id == ShotAsset.shot_id)
                .where(ShotAsset.asset_id.in_(counts), ShotScript.deleted_at.is_(None))
                .group_by(ShotAsset.asset_id)
            ):
                counts[asset_id] = count
        return counts

    def active_shot_ids(self, project_id, asset_id):
        return list(
            self.session.scalars(
                select(ShotAsset.shot_id)
                .join(ShotScript, ShotScript.id == ShotAsset.shot_id)
                .where(
                    ShotScript.project_id == project_id,
                    ShotAsset.asset_id == asset_id,
                    ShotScript.deleted_at.is_(None),
                )
                .order_by(ShotAsset.shot_id)
            )
        )

    def creation(self, key, *, for_update=False):
        statement = select(Asset).where(Asset.creation_key == key)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def removed_creation_hash(self, key):
        # 只查询本人项目中已删除作品的幂等信息，不将作品内容重新暴露给调用方。
        removed = Asset.__table__.alias("removed_creation")
        statement = (
            select(removed.c.creation_hash)
            .select_from(Project)
            .join(removed, removed.c.project_id == Project.id)
            .where(removed.c.creation_key == key, removed.c.deleted_at.is_not(None))
            .with_for_update()
        )
        return self.session.scalar(statement)

    def media(self, media_id):
        return self.session.scalar(select(MediaFile).where(MediaFile.id == media_id))

    def project_ids(self, asset_id):
        project_id = self.session.scalar(
            select(Asset.project_id).where(Asset.id == asset_id, Asset.deleted_at.is_(None))
        )
        return {project_id} if project_id is not None else set()
