from short_drama.domain import ShotVideo
from short_drama.schemas import ShotVideoCreate, ShotVideoRead, ShotVideoUpdate

from .shot_media_service import ShotMediaService


class ShotVideoService(ShotMediaService):
    model = ShotVideo
    create_schema = ShotVideoCreate
    update_schema = ShotVideoUpdate
    read_schema = ShotVideoRead
    media_kind = "video"
