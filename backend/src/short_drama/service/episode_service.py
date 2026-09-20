from sqlalchemy import func, select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.dao.episode_dao import EpisodeDAO
from short_drama.domain import Episode, Project
from short_drama.schemas import EpisodeCreate, EpisodeRead, EpisodeUpdate
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.episode import EpisodeDetail
from short_drama.schemas.project_creation import EpisodeCreateRequest, EpisodePatchRequest

from .base import BaseService, Page


class EpisodeService(BaseService):
    model = Episode
    create_schema = EpisodeCreate
    update_schema = EpisodeUpdate
    read_schema = EpisodeRead
    parent_model = Project
    parent_field = "project_id"

    def create_for_project(self, project_id, payload):
        project_id = parse_identifier(project_id)
        values = self._payload(EpisodeCreateRequest, payload)
        with self._transaction():
            project = self._require(Project, project_id)
            position = EpisodeDAO(self.session).last_position_for_update(project_id) + 1
            if position > 2**32 - 1:
                raise Conflict("Episode ordering space exhausted")
            values.update(project_id=project_id, position=position)
            values.setdefault("aspect", project.aspect)
            values.setdefault("style", project.style)
            return self._read(self.dao.create(self._creation_audit(values)))

    def _scoped_episode(self, project_id, episode_id, for_update=False):
        self._require(Project, project_id, for_update=for_update)
        episode = self.dao.get(parse_identifier(episode_id), for_update=for_update)
        if episode is None or episode.project_id != parse_identifier(project_id):
            raise NotFound("Episode does not exist in this project")
        return episode

    def get_for_project(self, project_id, episode_id):
        with self._transaction():
            episode = self._scoped_episode(project_id, episode_id)
            number = self.session.scalar(
                select(func.count())
                .select_from(Episode)
                .where(
                    Episode.project_id == episode.project_id,
                    Episode.position <= episode.position,
                )
            )
            return EpisodeDetail(**self._read(episode).model_dump(), episode_number=number)

    def list_for_project(self, project_id, offset=0, limit=20):
        self.dao.validate_pagination(offset, limit)
        with self._transaction():
            self._require(Project, project_id, for_update=False)
            filters = {"project_id": parse_identifier(project_id)}
            return Page(
                items=[self._read(row) for row in self.dao.list(offset, limit, filters)],
                total=self.dao.count(filters),
                offset=offset,
                limit=limit,
            )

    def update_for_project(self, project_id, episode_id, payload):
        values = self._payload(EpisodePatchRequest, payload)
        with self._transaction():
            episode = self._scoped_episode(project_id, episode_id, for_update=True)
            return self._read(self._apply_update(episode, values))

    def delete_for_project(self, project_id, episode_id):
        with self._transaction():
            episode = self._scoped_episode(project_id, episode_id, for_update=True)
            self.dao.delete(episode)
