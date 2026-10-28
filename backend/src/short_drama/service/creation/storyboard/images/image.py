from short_drama.domain import ShotImage
from short_drama.schemas import ShotImageCreate, ShotImageRead, ShotImageUpdate
from short_drama.service.creation.storyboard.management.media import ShotMediaService


class ShotImageService(ShotMediaService):
    model = ShotImage
    create_schema = ShotImageCreate
    update_schema = ShotImageUpdate
    read_schema = ShotImageRead
    media_kind = "image"
