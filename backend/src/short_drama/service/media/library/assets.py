"""持久媒体、显式采用及标准分镜视频完成后的自动确定。"""

from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict, WorkflowError
from short_drama.dao.media.library.assets import MediaAssetDAO
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    MediaAsset,
    MediaFile,
    ShotImage,
    ShotVideo,
)
from short_drama.schemas.media.library.assets import MediaAssetApply, MediaAssetRename
from short_drama.service.base import BaseService, utcnow
from short_drama.service.creation.storyboard.images.image import ShotImageService
from short_drama.service.creation.storyboard.videos.video import ShotVideoService
from short_drama.service.creation.writing.script.publication import public_voice_context
from short_drama.service.media.files.storage import StorageService


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
            "project_id": str(media.project_id) if media.project_id else None,
            "scope_user_id": str(media.scope_user_id) if media.scope_user_id else None,
            "media_type": asset.media_type,
            "name": asset.name,
            "row_version": str(asset.row_version),
            "url": url,
            "source": record.request_data.get("source"),
            "width": media.width,
            "height": media.height,
            "duration_ms": media.duration_ms,
            "native_quality": (media.video_metadata or {}).get("native_quality"),
            "byte_size": str(media.byte_size) if media.byte_size is not None else None,
            "created_at": asset.created_at,
            "updated_at": asset.updated_at,
        }

    def list(self, offset=0, limit=20, filters=None):
        if not 1 <= limit <= 100 or offset < 0:
            raise BusinessError("Invalid pagination")
        with self._transaction(read_only=True):
            rows, total = self.dao.history(filters or {}, offset, limit)
            return {
                "items": [self._dto(*row) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def detail(self, identifier):
        with self._transaction(read_only=True):
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
        with self._transaction():
            return self.apply_locked(identifier, payload)

    def apply_locked(self, identifier, payload):
        """Caller owns the project lock and transaction; adoption and receipt are atomic."""
        from short_drama.core.exceptions import BusinessError

        if not self.session.in_transaction():
            raise BusinessError("Native adoption requires an active transaction")
        parsed = MediaAssetApply.model_validate(payload)
        asset = self._require(MediaAsset, identifier, for_update=False)
        record = self._require(AIGenerationRecord, asset.record_id, for_update=False)
        kind = "video" if parsed.target.type == "shot_video" else "image"
        if asset.media_type != kind:
            raise BusinessError("Asset type does not match the target")
        if parsed.target.type == "shot_image":
            return self._apply_shot_image(asset, record, parsed)
        if parsed.target.type == "asset_image":
            return self._apply_asset_image(asset, parsed)
        if parsed.target.type == "shot_video":
            return self._apply_shot_video(asset, record, parsed)

    def confirm_generated_shot_video_locked(
        self, task: AsyncTask, record: AIGenerationRecord
    ) -> dict[str, object] | None:
        """项目锁与完成事务由调用方持有；历史任务也可在受控事务内幂等补全。"""
        request = record.request_data
        if (
            task.service_type != "video"
            or (request.get("source") or {}).get("scene") != "shot_video"
            or "canvas_request" in request
            or record.config_snapshot.get("agent_managed")
        ):
            return None
        if not self.session.in_transaction():
            raise BusinessError("Video completion requires an active transaction")
        data = record.response_data or {}
        receipt = data.get("shot_video_auto_apply")
        if receipt is not None:
            return receipt
        entries = data.get("media_manifest", [])
        if (
            record.status != "succeeded"
            or data.get("expected_count", 1) != 1
            or len(entries) != 1
            or not entries[0].get("saved")
            or entries[0].get("ignored")
        ):
            return None
        try:
            with self.session.begin_nested():
                result = self._confirm_generated_shot_video(task, record)
            receipt = {
                "status": "applied",
                **result,
                "source_row_version": request["source_snapshot"]["row_version"],
            }
        except BusinessError as error:
            # 业务来源失效不丢弃已付费产物；数据库异常继续由任务恢复处理。
            receipt = {"status": "skipped", "code": error.code}
        record.response_data = {**data, "shot_video_auto_apply": receipt}
        record.updated_at = utcnow()
        self.session.flush()
        return receipt

    def _confirm_generated_shot_video(
        self, task: AsyncTask, record: AIGenerationRecord
    ) -> dict[str, object]:
        from short_drama.service.creation.storyboard.videos.video_context import video_context_hash
        from short_drama.service.generations.admission.context import GenerationContextService
        from short_drama.service.generations.runtime.access import may_submit

        if task.cancel_requested:
            raise WorkflowError("generation_cancelled", "任务已取消，生成视频仅保留在历史中")
        if not may_submit(self.session, task):
            raise WorkflowError("generation_access_revoked", "生成者已无权更新此分镜")
        source = record.request_data.get("source") or {}
        captured = record.request_data.get("source_snapshot") or {}
        creation, shot, _assets, _snapshot, shot_hash = GenerationContextService(
            self.session
        ).locked_shot_context(source["shot_id"])
        if (
            record.task_id != task.id
            or creation.id != task.project_id
            or str(creation.id) != str(source.get("project_id"))
        ):
            raise WorkflowError("source_mismatch", "生成视频与当前分镜不属于同一项目")
        image = self.session.scalar(
            select(ShotImage).where(ShotImage.shot_id == shot.id).with_for_update()
        )
        current = self.session.scalar(
            select(ShotVideo).where(ShotVideo.shot_id == shot.id).with_for_update()
        )
        digest = video_context_hash(
            shot_hash,
            image.media_id if image else None,
            shot.video_prompt,
            shot.video_settings,
            session=self.session,
            shot=shot,
        )
        if (
            captured.get("context_hash") != digest
            or source.get("context_hash") != digest
            or not image
            or str(image.media_id) != str(captured.get("reference_media_id"))
        ):
            raise WorkflowError("shot_version_conflict", "分镜、参考图或声音已变化，保留原确定视频")
        previous_record = (
            self.session.scalar(
                select(AIGenerationRecord)
                .join(MediaAsset, MediaAsset.record_id == AIGenerationRecord.id)
                .where(MediaAsset.media_id == current.media_id)
            )
            if current
            else None
        )
        previous_task = (
            self.session.get(AsyncTask, previous_record.task_id) if previous_record else None
        )
        if previous_task and (previous_task.created_at, previous_task.id) > (
            task.created_at,
            task.id,
        ):
            raise WorkflowError("shot_video_superseded", "已有更新生成的视频，旧任务仅保留历史")
        previous_receipt = (
            (previous_record.response_data or {}).get("shot_video_auto_apply", {})
            if previous_record
            else {}
        )
        same_automatic_source = (
            previous_receipt.get("status") == "applied"
            and previous_receipt.get("row_version") == str(shot.row_version)
            and previous_receipt.get("media_id") == str(current.media_id)
            and previous_receipt.get("source_row_version") == captured.get("row_version")
        )
        if source.get("row_version") != captured.get("row_version") or (
            captured.get("row_version") != str(shot.row_version) and not same_automatic_source
        ):
            raise WorkflowError("shot_version_conflict", "分镜版本已变化，保留原确定视频")
        asset = self.session.scalar(
            select(MediaAsset).where(
                MediaAsset.record_id == record.id, MediaAsset.output_index == 1
            )
        )
        if asset is None or asset.media_type != "video":
            raise WorkflowError("video_not_ready", "生成视频尚未完整保存")
        media = self._require(MediaFile, asset.media_id, for_update=False)
        if (
            media.project_id != task.project_id
            or media.scope_user_id is not None
            or media.created_by != task.initiated_by
        ):
            raise WorkflowError("source_mismatch", "生成视频的归属与任务不一致")
        parsed = MediaAssetApply.model_validate(
            {
                "target": {"type": "shot_video", "id": str(shot.id)},
                "expected_media_id": str(current.media_id) if current else None,
                "expected_row_version": str(shot.row_version),
                "expected_context_hash": digest,
            }
        )
        return self._apply_shot_video(asset, record, parsed)

    def _apply_shot_video(self, asset, record, parsed):
        from short_drama.service.creation.storyboard.videos.video_context import video_context_hash
        from short_drama.service.generations.admission.context import GenerationContextService

        native = (record.request_data.get("source_snapshot") or {}).get("native_speech")
        if native is not None:
            media = self._require(MediaFile, asset.media_id, for_update=False)
            if not (media.video_metadata or {}).get("native_quality", {}).get("technical_pass"):
                raise WorkflowError(
                    "native_audio_invalid", "原生视频缺少有效对白音轨，不能采用；请核对候选", 422
                )

        creation, shot, _assets, _snapshot, shot_hash = GenerationContextService(
            self.session
        ).locked_shot_context(parsed.target.id)
        image = self.session.scalar(
            select(ShotImage).where(ShotImage.shot_id == shot.id).with_for_update()
        )
        current = self.session.scalar(
            select(ShotVideo).where(ShotVideo.shot_id == shot.id).with_for_update()
        )
        digest = video_context_hash(
            shot_hash,
            image.media_id if image else None,
            shot.video_prompt,
            shot.video_settings,
            session=self.session,
            shot=shot,
        )

        def response():
            return {
                "target": parsed.target.model_dump(mode="json"),
                "media_id": str(asset.media_id),
                "row_version": str(shot.row_version),
                "storyboard_version": str(creation.storyboard_version),
                "context_hash": digest,
            }

        if current and current.media_id == asset.media_id and current.context_hash == digest:
            return response()
        # Legacy generic adoption also uses this path; context tokens are now required
        # at all entry points, rather than silently bypassing source/version checks.
        if (
            parsed.expected_row_version != shot.row_version
            or parsed.expected_context_hash != digest
        ):
            raise WorkflowError(
                "shot_version_conflict", "分镜、提示词或参考图已变化，请重新核对后采用"
            )
        if (current.media_id if current else None) != parsed.expected_media_id:
            raise WorkflowError("shot_version_conflict", "当前视频已变化，请重新选择")
        request = record.request_data
        source, captured = request.get("source") or {}, request.get("source_snapshot") or {}
        original = request.get("parameters", {})
        provided = parsed.parameters.model_dump(exclude_none=True)
        for name, key in (
            (("resolution", "resolution"), ("duration", "duration_ms"))
            if source.get("scene") == "shot_video"
            else ()
        ):
            if original.get(key) and provided.get(name) and original[key] != provided[name]:
                raise WorkflowError(
                    "generation_parameters_conflict", "采用参数必须与生成记录一致", 422
                )
        media = self._require(MediaFile, asset.media_id, for_update=False)
        resolution = provided.get("resolution") or original.get("resolution")
        duration = provided.get("duration") or media.duration_ms or original.get("duration_ms")
        if not resolution or not duration:
            raise WorkflowError("missing_adoption_parameters", "请补全视频清晰度和实际时长", 422)
        values = ShotVideoService.create_schema.model_validate(
            {
                "project_id": shot.project_id,
                "shot_id": shot.id,
                "media_id": asset.media_id,
                "model_id": record.config_id,
                "prompt": request.get("input", {}).get("prompt", ""),
                "resolution": resolution,
                "duration": duration,
                "context_hash": captured.get("context_hash"),
                "first_frame_media_id": request.get("input", {}).get("first_frame_media_id"),
            }
        ).model_dump()
        ShotVideoService(self.session)._write_confirmed(
            values, shot, existing=current, historical=True
        )
        if captured.get("native_speech") is not None:
            media.video_metadata = {
                **(media.video_metadata or {}),
                "adopted_native_speech": public_voice_context(captured["native_speech"]),
            }
        self.session.flush()
        return response()

    def _apply_shot_image(self, asset, record, parsed):
        from short_drama.service.generations.admission.context import GenerationContextService

        creation, shot, _assets, _snapshot, digest = GenerationContextService(
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
                "storyboard_version": str(creation.storyboard_version),
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
        if not all(parameters.get(k) for k in ("layout", "resolution")):
            raise WorkflowError("missing_adoption_parameters", "请补全图片布局、画幅和分辨率", 422)
        service = ShotImageService(self.session)
        values = service.create_schema.model_validate(
            {
                "project_id": shot.project_id,
                "shot_id": shot.id,
                "media_id": asset.media_id,
                "model_id": record.config_id,
                "prompt": request.get("input", {}).get("prompt", ""),
                "layout": parameters["layout"],
                "aspect": parameters.get("aspect"),
                "resolution": parameters["resolution"],
                "context_hash": digest,
            }
        ).model_dump()
        service._write_confirmed(values, shot, existing=current, historical=True)
        self.session.flush()
        return response()

    def _apply_asset_image(self, asset, parsed):
        from short_drama.service.creation.assets.images.image import AssetImageService

        service = AssetImageService(self.session, self.settings, self.storage)
        target, _media = service.confirm_locked(
            parsed.target.id,
            {
                "row_version": parsed.expected_row_version,
                "media_id": asset.media_id,
                "expected_media_id": parsed.expected_media_id,
                "acknowledge_stale_source": parsed.acknowledge_stale_source,
            },
            add_generated_candidate=True,
        )
        return {
            "target": parsed.target.model_dump(mode="json"),
            "media_id": str(asset.media_id),
            "row_version": str(target.row_version),
        }
