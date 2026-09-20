from short_drama.domain import Episode, Project
from short_drama.schemas import EpisodeCreate, EpisodeRead, EpisodeUpdate

from .base import BaseService


class EpisodeService(BaseService):
    model = Episode
    create_schema = EpisodeCreate
    update_schema = EpisodeUpdate
    read_schema = EpisodeRead
    parent_model = Project
    parent_field = "project_id"
