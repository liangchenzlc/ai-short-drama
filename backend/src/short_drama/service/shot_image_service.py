from short_drama.domain import ShotImage
from short_drama.schemas import ShotImageCreate, ShotImageRead, ShotImageUpdate

from .shot_media_service import ShotMediaService


class ShotImageService(ShotMediaService):
    model = ShotImage
    create_schema = ShotImageCreate
    update_schema = ShotImageUpdate
    read_schema = ShotImageRead
    media_kind = "image"
