from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict
from short_drama.domain import AIModelConfig, MediaRecycleBin, ShotImage, ShotScript, ShotVideo
from short_drama.schemas import MediaRecycleBinCreate, MediaRecycleBinRead

from .base import BaseService


class MediaRecycleBinService(BaseService):
    model = MediaRecycleBin
    create_schema = MediaRecycleBinCreate
    read_schema = MediaRecycleBinRead
    parent_model = ShotScript
    parent_field = "shot_id"

    def _validate_create(self, values):
        kind = "video" if values.get("duration") is not None else "image"
        self._validate_media(values["media_id"], kind)
        if values.get("model_id") is not None:
            model = self._require(AIModelConfig, values["model_id"])
            if model.service_type != kind:
                raise BusinessError("Model type does not match recycled media")
        for model in (ShotImage, ShotVideo):
            current = self.session.scalar(
                select(model.id)
                .where(model.shot_id == values["shot_id"], model.media_id == values["media_id"])
                .with_for_update()
            )
            if current is not None:
                raise Conflict("Confirmed media cannot also be in the recycle bin")

    def restore(self, identifier):
        # Import here to keep the two public services independent at module load.
        from .shot_image_service import ShotImageService
        from .shot_video_service import ShotVideoService

        with self._transaction():
            entry = self._get_locked(identifier)
            shot = self._require(ShotScript, entry.shot_id)
            service = (
                ShotVideoService(self.session)
                if entry.duration is not None
                else ShotImageService(self.session)
            )
            values = {
                name: getattr(entry, name)
                for name in service.create_schema.model_fields
                if name not in {"episode_id", "context_hash"}
            }
            values["episode_id"] = shot.episode_id
            if service.media_kind == "image":
                # Recycle records have no historical context digest. Never borrow the
                # current image's digest: restored images must remain marked stale.
                values["context_hash"] = None
            values = service.create_schema.model_validate(values).model_dump()
            existing = self.session.scalar(
                select(service.model)
                .where(service.model.shot_id == shot.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            return service._read(
                service._write_confirmed(
                    values,
                    shot,
                    existing=existing,
                    historical=True,
                )
            )
