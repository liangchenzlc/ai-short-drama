from sqlalchemy import String, cast, func, or_, select

from short_drama.domain.asset import Asset
from short_drama.domain.episode import Episode
from short_drama.domain.episode_asset import EpisodeAsset
from short_drama.domain.global_asset import GlobalAsset
from short_drama.domain.media_file import MediaFile
from short_drama.domain.project_asset import ProjectAsset
from short_drama.domain.shot_asset import ShotAsset
from short_drama.domain.shot_script import ShotScript


class AssetLibraryDAO:
    scopes = {
        "global": (GlobalAsset, None),
        "project": (ProjectAsset, "project_id"),
        "episode": (EpisodeAsset, "episode_id"),
    }

    def __init__(self, session):
        self.session = session

    def _scope(self, kind):
        return self.scopes[kind]

    def scope_conditions(self, kind, parent_id):
        model, parent_field = self._scope(kind)
        return [] if parent_field is None else [getattr(model, parent_field) == parent_id]

    def link(self, kind, parent_id, asset_id, *, for_update=False):
        model, _ = self._scope(kind)
        statement = select(model).where(
            *self.scope_conditions(kind, parent_id), model.asset_id == asset_id
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def links(self, kind, parent_id, *, asset_kind=None, query="", offset=0, limit=20):
        model, _ = self._scope(kind)
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
            select(model, Asset, MediaFile)
            .join(Asset, Asset.id == model.asset_id)
            .outerjoin(MediaFile, MediaFile.id == Asset.media_id)
            .where(*conditions)
        )
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(
            statement.order_by(model.position, model.id).offset(offset).limit(limit)
        ).all()
        return rows, total

    def next_position(self, kind, parent_id):
        model, _ = self._scope(kind)
        value = self.session.scalar(
            select(func.max(model.position)).where(*self.scope_conditions(kind, parent_id))
        )
        return (value or 0) + 1

    def reference_count(self, asset_id):
        total = 0
        for model in (GlobalAsset, ProjectAsset, EpisodeAsset):
            total += self.session.scalar(
                select(func.count()).select_from(model).where(model.asset_id == asset_id)
            )
        shot_query = (
            select(func.count())
            .select_from(ShotAsset)
            .join(ShotScript, ShotScript.id == ShotAsset.shot_id)
            .where(ShotAsset.asset_id == asset_id)
        )
        if hasattr(ShotScript, "deleted_at"):
            shot_query = shot_query.where(ShotScript.deleted_at.is_(None))
        return total + self.session.scalar(shot_query)

    def reference_counts(self, asset_ids):
        counts = {asset_id: 0 for asset_id in asset_ids}
        if not counts:
            return counts
        for model in (GlobalAsset, ProjectAsset, EpisodeAsset):
            rows = self.session.execute(
                select(model.asset_id, func.count())
                .where(model.asset_id.in_(counts))
                .group_by(model.asset_id)
            )
            for asset_id, count in rows:
                counts[asset_id] += count
        shot_query = (
            select(ShotAsset.asset_id, func.count())
            .join(ShotScript, ShotScript.id == ShotAsset.shot_id)
            .where(ShotAsset.asset_id.in_(counts))
            .group_by(ShotAsset.asset_id)
        )
        if hasattr(ShotScript, "deleted_at"):
            shot_query = shot_query.where(ShotScript.deleted_at.is_(None))
        for asset_id, count in self.session.execute(shot_query):
            counts[asset_id] += count
        return counts

    def active_shot_ids(self, episode_id, asset_id):
        statement = (
            select(ShotAsset.shot_id)
            .join(ShotScript, ShotScript.id == ShotAsset.shot_id)
            .where(ShotAsset.episode_id == episode_id, ShotAsset.asset_id == asset_id)
        )
        if hasattr(ShotScript, "deleted_at"):
            statement = statement.where(ShotScript.deleted_at.is_(None))
        return list(self.session.scalars(statement.order_by(ShotAsset.shot_id)))

    def creation(self, key, *, for_update=False):
        statement = select(Asset).where(Asset.creation_key == key)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def media(self, media_id):
        return self.session.scalar(select(MediaFile).where(MediaFile.id == media_id))

    def is_global(self, asset_id):
        return (
            self.session.scalar(
                select(GlobalAsset.id).where(GlobalAsset.asset_id == asset_id).limit(1)
            )
            is not None
        )

    def project_ids(self, asset_id):
        direct = set(
            self.session.scalars(
                select(ProjectAsset.project_id).where(ProjectAsset.asset_id == asset_id)
            )
        )
        episodes = self.session.scalars(
            select(Episode.project_id)
            .join(EpisodeAsset, EpisodeAsset.episode_id == Episode.id)
            .where(EpisodeAsset.asset_id == asset_id)
        )
        return direct | set(episodes)
