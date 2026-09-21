from short_drama.dao.episode_writing_dao import VersionedDocumentDAO
from short_drama.domain import Episode, EpisodeNovel
from short_drama.schemas import EpisodeNovelCreate, EpisodeNovelRead, EpisodeNovelUpdate

from .base import BaseService


class EpisodeNovelService(BaseService):
    model = EpisodeNovel
    create_schema = EpisodeNovelCreate
    update_schema = EpisodeNovelUpdate
    read_schema = EpisodeNovelRead
    parent_model = Episode
    parent_field = "episode_id"

    def __init__(self, session):
        super().__init__(session)
        self.dao = VersionedDocumentDAO(session, self.model)
