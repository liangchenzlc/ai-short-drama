from short_drama.domain import Episode, ShotScript
from short_drama.schemas import ShotScriptCreate, ShotScriptRead, ShotScriptUpdate

from .base import BaseService


class ShotScriptService(BaseService):
    model = ShotScript
    create_schema = ShotScriptCreate
    update_schema = ShotScriptUpdate
    read_schema = ShotScriptRead
    parent_model = Episode
    parent_field = "episode_id"
