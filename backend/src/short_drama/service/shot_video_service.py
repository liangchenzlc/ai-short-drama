from short_drama.domain import ShotVideo
from short_drama.schemas import ShotVideoCreate, ShotVideoRead, ShotVideoUpdate

from .shot_media_service import ShotMediaService


class ShotVideoService(ShotMediaService):
    model = ShotVideo
    create_schema = ShotVideoCreate
    update_schema = ShotVideoUpdate
    read_schema = ShotVideoRead
    media_kind = "video"

    def _check_values(self, values, shot, historical=False, previous=None):
        super()._check_values(values, shot, historical, previous)
        from short_drama.core.exceptions import WorkflowError

        media = self._validate_media(values["media_id"], "video")
        quality = (media.video_metadata or {}).get("native_quality")
        if quality is not None and not quality.get("technical_pass"):
            raise WorkflowError("native_audio_invalid", "原生视频没有通过音轨检查，不能采用", 422)
