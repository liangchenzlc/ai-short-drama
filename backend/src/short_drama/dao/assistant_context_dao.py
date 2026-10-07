"""Read current, scoped works while the caller owns the project transaction."""

from sqlalchemy import select

from short_drama.dao.canvas_dao import CanvasDAO
from short_drama.dao.episode_writing_dao import EpisodeWritingDAO
from short_drama.domain import (
    Asset,
    Episode,
    EpisodeAsset,
    MediaFile,
    Project,
    ProjectCanvas,
    ShotScript,
)


class AssistantContextDAO:
    def __init__(self, session):
        self.session = session

    def project(self, identifier):
        return self.session.scalar(select(Project).where(Project.id == identifier))

    def episode(self, project_id, identifier):
        return self.session.scalar(
            select(Episode)
            .where(Episode.id == identifier, Episode.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def writing(self, episode):
        dao = EpisodeWritingDAO(self.session)
        novel = dao.novel(episode.id, for_update=True)
        script = (
            dao.script(episode.id, episode.editing_script_id, for_update=True)
            if episode.editing_script_id
            else None
        )
        return novel, script

    def assets(self, episode_id):
        return list(
            self.session.scalars(
                select(Asset)
                .join(EpisodeAsset, EpisodeAsset.asset_id == Asset.id)
                .where(EpisodeAsset.episode_id == episode_id)
                .order_by(Asset.id)
                .limit(101)
                .with_for_update()
            )
        )

    def asset(self, project_id, episode_id, identifier):
        return self.session.scalar(
            select(Asset)
            .join(EpisodeAsset, EpisodeAsset.asset_id == Asset.id)
            .where(
                Asset.id == identifier,
                Asset.project_id == project_id,
                EpisodeAsset.episode_id == episode_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def shots(self, episode_id):
        return list(
            self.session.scalars(
                select(ShotScript)
                .where(ShotScript.episode_id == episode_id, ShotScript.deleted_at.is_(None))
                .order_by(ShotScript.position, ShotScript.id)
                .limit(101)
                .with_for_update()
            )
        )

    def shot(self, episode_id, identifier):
        return self.session.scalar(
            select(ShotScript)
            .where(
                ShotScript.id == identifier,
                ShotScript.episode_id == episode_id,
                ShotScript.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def canvas(self, project_id, source_key):
        return self.session.scalar(
            select(ProjectCanvas)
            .where(
                ProjectCanvas.project_id == project_id,
                ProjectCanvas.source_key == source_key,
                ProjectCanvas.archived_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def canvas_nodes(self, canvas_id):
        from short_drama.domain.canvas import CanvasNode

        return CanvasDAO(self.session).children(CanvasNode, canvas_id, active=True, lock=True)

    def media(self, identifier):
        # The actor-scoped Session also excludes other users' unpublished outputs.
        return self.session.scalar(
            select(MediaFile)
            .where(MediaFile.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
