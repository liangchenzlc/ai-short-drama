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
