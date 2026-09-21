"""Permanent generated media and explicit optimistic adoption into real targets."""

from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict, WorkflowError
from short_drama.dao.media_asset_dao import MediaAssetDAO
from short_drama.domain import (
    AIGenerationRecord,
    Episode,
    MediaAsset,
    MediaFile,
    ShotImage,
    ShotScript,
    ShotVideo,
)
from short_drama.schemas.media_asset import MediaAssetApply, MediaAssetRename

from .base import BaseService, utcnow
from .shot_image_service import ShotImageService
from .shot_video_service import ShotVideoService
from .storage_service import StorageService


class MediaAssetService(BaseService):
    model = MediaAsset

    def __init__(self, session, settings, storage):
        super().__init__(session)
        self.settings = settings
        self.storage = storage
        self.dao = MediaAssetDAO(session)

    def _dto(self, asset, record=None, media=None):
        record = record or self._require(AIGenerationRecord, asset.record_id, for_update=False)
        media = media or self._require(MediaFile, asset.media_id, for_update=False)
        url = (
            StorageService(self.storage, self.settings).download_url(media.storage_locator)
            if self.storage is not None
            else None
        )
        return {
            "asset_id": str(asset.id),
            "id": str(asset.id),
            "record_id": str(record.id),
            "generation_id": str(record.task_id),
            "media_id": str(media.id),
            "media_type": asset.media_type,
            "name": asset.name,
            "row_version": str(asset.row_version),
            "url": url,
            "source": record.request_data.get("source"),
            "width": media.width,
            "height": media.height,
            "duration_ms": media.duration_ms,
            "byte_size": str(media.byte_size) if media.byte_size is not None else None,
            "created_at": asset.created_at,
            "updated_at": asset.updated_at,
        }

    def list(self, offset=0, limit=20, filters=None):
        if not 1 <= limit <= 100 or offset < 0:
            raise BusinessError("Invalid pagination")
        with self._transaction():
            rows, total = self.dao.history(filters or {}, offset, limit)
            return {
                "items": [self._dto(*row) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def detail(self, identifier):
        with self._transaction():
            return self._dto(self._require(MediaAsset, identifier, for_update=False))

    get = detail

    def rename(self, identifier, payload):
        parsed = MediaAssetRename.model_validate(payload)
        with self._transaction():
            asset = self._require(MediaAsset, identifier)
            if asset.row_version != parsed.row_version:
                raise Conflict("Asset version is stale")
            if asset.name != parsed.name:
                if asset.row_version == 2**64 - 1:
                    raise Conflict("Asset version is exhausted")
                asset.name = parsed.name
                asset.row_version += 1
                asset.updated_at = utcnow()
                self.session.flush()
            return self._dto(asset)

    def apply(self, identifier, payload):
        parsed = MediaAssetApply.model_validate(payload)
        with self._transaction():
            asset = self._require(MediaAsset, identifier, for_update=False)
            record = self._require(AIGenerationRecord, asset.record_id, for_update=False)
            kind = "video" if parsed.target.type == "shot_video" else "image"
            if asset.media_type != kind:
                raise BusinessError("Asset type does not match the target")
            request = record.request_data
            if parsed.target.type == "shot_image":
                return self._apply_shot_image(asset, record, parsed)
            if parsed.target.type == "asset_image":
                return self._apply_asset_image(asset, parsed)
            # Provider-native fields have protocol-specific units and representations.
            # Adoption uses only validated business parameters and explicit overrides.
            parameters = {
                **request.get("parameters", {}),
                **parsed.parameters.model_dump(exclude_none=True),
            }
            prompt = request.get("input", {}).get("prompt", "")
            episode_id = self.session.scalar(
                select(ShotScript.episode_id).where(ShotScript.id == parsed.target.id)
            )
            if episode_id is None:
                from short_drama.core.exceptions import NotFound

                raise NotFound("Shot does not exist")
            self._require(Episode, episode_id)
            shot = self._require(ShotScript, parsed.target.id)
            model = ShotImage if kind == "image" else ShotVideo
            current = self.session.scalar(
                select(model).where(model.shot_id == shot.id).with_for_update()
            )
            if current is not None and current.media_id == asset.media_id:
                return {
                    "target": parsed.target.model_dump(mode="json"),
                    "media_id": str(asset.media_id),
                }
            if (current.media_id if current else None) != parsed.expected_media_id:
                raise Conflict("Target media changed; refresh before adopting")
            values = {
                "episode_id": shot.episode_id,
                "shot_id": shot.id,
                "media_id": asset.media_id,
                "model_id": record.config_id,
                "prompt": prompt,
            }
            if not parameters.get("resolution"):
                raise BusinessError("Provide the resolution before adopting this result")
            values["resolution"] = parameters["resolution"]
            media = self._require(MediaFile, asset.media_id, for_update=False)
            duration = (
                parsed.parameters.duration
                or media.duration_ms
                or request.get("parameters", {}).get("duration_ms")
            )
            if not duration:
                raise BusinessError(
                    "Provide actual duration in milliseconds before adopting this video"
                )
            values["duration"] = duration
            service = ShotVideoService(self.session)
            values = service.create_schema.model_validate(values).model_dump()
            service._write_confirmed(values, shot, existing=current, historical=True)
            return {
                "target": parsed.target.model_dump(mode="json"),
                "media_id": str(asset.media_id),
            }

    def _apply_shot_image(self, asset, record, parsed):
        from .generation_context_service import GenerationContextService

        episode, shot, _assets, _snapshot, digest = GenerationContextService(
            self.session
        ).locked_shot_context(parsed.target.id)
        current = self.session.scalar(
            select(ShotImage).where(ShotImage.shot_id == shot.id).with_for_update()
        )

        def response():
            return {
                "target": parsed.target.model_dump(mode="json"),
                "media_id": str(asset.media_id),
                "row_version": str(shot.row_version),
                "storyboard_version": str(episode.storyboard_version),
                "context_hash": digest,
            }

        if current and current.media_id == asset.media_id and current.context_hash == digest:
            return response()
        if (
            shot.row_version != parsed.expected_row_version
            or digest != parsed.expected_context_hash
        ):
            raise WorkflowError("shot_version_conflict", "分镜内容已变化，请重新核对后采用")
        if (current.media_id if current else None) != parsed.expected_media_id:
            raise WorkflowError("shot_version_conflict", "当前图片已变化，请重新选择")
        request = record.request_data
        source = request.get("source") or {}
        captured = request.get("source_snapshot") or {}
        if (
            source.get("scene") != "shot_image"
            or str(source.get("shot_id")) != str(shot.id)
            or captured.get("context_hash") != digest
        ) and not parsed.acknowledge_stale_source:
            raise WorkflowError(
                "stale_generation_source", "此图来自其他或较早的创作内容，请明确确认采用"
            )
        original = {**request.get("parameters", {})}
        if source.get("layout"):
            original["layout"] = source["layout"]
        provided = parsed.parameters.model_dump(exclude_none=True)
        for key in ("layout", "aspect", "resolution"):
            if original.get(key) and provided.get(key) and original[key] != provided[key]:
                raise WorkflowError(
                    "generation_parameters_conflict", "采用参数必须与生成记录一致", 422
                )
        parameters = {**provided, **{k: v for k, v in original.items() if v is not None}}
        if not all(parameters.get(k) for k in ("layout", "aspect", "resolution")):
            raise WorkflowError("missing_adoption_parameters", "请补全图片布局、画幅和分辨率", 422)
        service = ShotImageService(self.session)
        values = service.create_schema.model_validate(
            {
                "episode_id": shot.episode_id,
                "shot_id": shot.id,
                "media_id": asset.media_id,
                "model_id": record.config_id,
                "prompt": request.get("input", {}).get("prompt", ""),
                "layout": parameters["layout"],
                "aspect": parameters["aspect"],
                "resolution": parameters["resolution"],
                "context_hash": digest,
            }
        ).model_dump()
        service._write_confirmed(values, shot, existing=current, historical=True)
        self.session.flush()
        return response()

    def _apply_asset_image(self, asset, parsed):
        from .asset_image_service import AssetImageService

        service = AssetImageService(self.session, self.settings, self.storage)
        target, _media = service.confirm_locked(
            parsed.target.id,
            {
                "row_version": parsed.expected_row_version,
                "media_id": asset.media_id,
                "expected_media_id": parsed.expected_media_id,
                "confirm_shared": parsed.confirm_shared,
            },
            add_generated_candidate=True,
        )
        return {
            "target": parsed.target.model_dump(mode="json"),
            "media_id": str(asset.media_id),
            "row_version": str(target.row_version),
        }
