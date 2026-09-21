"""Episode-scoped reads and versioned internal document writes."""

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.domain import Episode, EpisodeNovel, EpisodeScript
from short_drama.schemas.base import UINT64_MAX

from .base import BaseDAO


def bump_writing_version(episode):
    if episode.content_version >= UINT64_MAX:
        raise Conflict("Writing version exhausted")
    episode.content_version += 1


class EpisodeWritingDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, Episode)

    def scoped_episode(self, project_id, episode_id):
        # All document writers share this mutex, including first insert/confirmation.
        row = self.session.scalar(
            select(Episode)
            .where(Episode.id == episode_id, Episode.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("Episode does not exist in this project")
        return row

    def novel(self, episode_id):
        return self.session.scalar(
            select(EpisodeNovel)
            .where(EpisodeNovel.episode_id == episode_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def script(self, episode_id, script_id):
        row = self.session.scalar(
            select(EpisodeScript)
            .where(EpisodeScript.id == script_id, EpisodeScript.episode_id == episode_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("Script does not exist in this episode")
        return row

    def confirmed(self, episode_id):
        return self.session.scalar(
            select(EpisodeScript)
            .where(EpisodeScript.episode_id == episode_id, EpisodeScript.state == "confirmed")
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def next_position(self, episode_id):
        maximum = (
            self.session.scalar(
                select(EpisodeScript.position)
                .where(EpisodeScript.episode_id == episode_id)
                .order_by(EpisodeScript.position.desc())
                .limit(1)
                .with_for_update()
            )
            or 0
        )
        if maximum == 2**32 - 1:
            raise Conflict("Script ordering space exhausted")
        return maximum + 1


class VersionedDocumentDAO(BaseDAO):
    """Internal CRUD/generation services also invalidate open editor snapshots.

    Services own the enclosing transaction and lock episode before child records.
    Direct writing API uses BaseDAO and increments once for its aggregate mutation.
    """

    def _episode(self, episode_id):
        episode = BaseDAO(self.session, Episode).get(episode_id, for_update=True)
        if episode is None:
            raise NotFound("Episode does not exist")
        return episode

    def create(self, values):
        episode = self._episode(values["episode_id"])
        row = super().create(values)
        if self.model is EpisodeScript and episode.editing_script_id is None:
            episode.editing_script_id = row.id
        bump_writing_version(episode)
        self.session.flush()
        return row

    def update(self, entity, values):
        episode = self._episode(entity.episode_id)
        changed = any(getattr(entity, key) != value for key, value in values.items())
        row = super().update(entity, values)
        if changed:
            bump_writing_version(episode)
            self.session.flush()
        return row

    def delete(self, entity):
        episode = self._episode(entity.episode_id)
        if self.model is EpisodeScript and episode.editing_script_id == entity.id:
            episode.editing_script_id = None
        bump_writing_version(episode)
        super().delete(entity)
