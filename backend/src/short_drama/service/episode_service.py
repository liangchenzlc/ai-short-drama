from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.episode_dao import EpisodeDAO
from short_drama.domain import Episode, MediaFile, Project
from short_drama.schemas import EpisodeCreate, EpisodeRead, EpisodeUpdate
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.episode import EpisodeDetail
from short_drama.schemas.project_creation import EpisodeCreateRequest, EpisodePatchRequest

from .base import BaseService, Page
from .storage_service import StorageService


class EpisodeService(BaseService):
    model = Episode
    create_schema = EpisodeCreate
    update_schema = EpisodeUpdate
    read_schema = EpisodeRead
    parent_model = Project
    parent_field = "project_id"

    def __init__(self, session: Session, storage: StorageService | None = None) -> None:
        super().__init__(session)
        self.episodes = EpisodeDAO(session)
        self.storage = storage

    def _read_with_cover(self, episode: Episode, media: MediaFile | None = None) -> EpisodeRead:
        result = self._read(episode)
        if media is not None and self.storage is not None:
            result.cover_url = self.storage.download_url(media.storage_locator)
        return result

    def create_for_project(self, project_id, payload):
        project_id = parse_identifier(project_id)
        values = self._payload(EpisodeCreateRequest, payload)
        with self._transaction():
            project = self._require(Project, project_id)
            if project.workspace_mode != "standard":
                raise WorkflowError(
                    "standard_mode_required", "Episodes belong to standard-mode projects", 409
                )
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
        with self._transaction(read_only=True):
            episode = self._scoped_episode(project_id, episode_id)
            number = self.session.scalar(
                select(func.count())
                .select_from(Episode)
                .where(
                    Episode.project_id == episode.project_id,
                    Episode.position <= episode.position,
                )
            )
            media = self.episodes.covers([episode.id]).get(episode.id)
            return EpisodeDetail(
                **self._read_with_cover(episode, media).model_dump(), episode_number=number
            )

    def list_for_project(self, project_id, offset=0, limit=20):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            self._require(Project, project_id, for_update=False)
            filters = {"project_id": parse_identifier(project_id)}
            rows = self.dao.list(offset, limit, filters)
            covers = self.episodes.covers([row.id for row in rows])
            return Page(
                items=[self._read_with_cover(row, covers.get(row.id)) for row in rows],
                total=self.dao.count(filters),
                offset=offset,
                limit=limit,
            )

    def update_for_project(self, project_id, episode_id, payload):
        values = self._payload(EpisodePatchRequest, payload)
        version = values.pop("row_version", None)
        with self._transaction():
            episode = self._scoped_episode(project_id, episode_id, for_update=True)
            if self.session.info.get("actor") and version != episode.row_version:
                raise WorkflowError(
                    "episode_version_conflict",
                    "Episode settings changed; reload and merge your edits",
                    409,
                )
            if any(getattr(episode, k) != v for k, v in values.items()):
                values["row_version"] = episode.row_version + 1
            return self._read(self._apply_update(episode, values))

    def delete_for_project(self, project_id, episode_id):
        with self._transaction():
            episode = self._scoped_episode(project_id, episode_id, for_update=True)
            self.dao.delete(episode)
