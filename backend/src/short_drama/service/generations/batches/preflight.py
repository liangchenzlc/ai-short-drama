"""批量请求的来源权限、能力与预算预检。"""

from sqlalchemy import String, cast, or_, select

from short_drama.ai import GenerationError, validate_request
from short_drama.core.exceptions import (
    BusinessError,
    Conflict,
    GenerationRequestError,
    WorkflowError,
)
from short_drama.dao.generations.runtime.tasks import source_conditions
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AssetImageCandidate,
    AsyncTask,
    MediaAsset,
    Project,
    ShotImage,
    ShotScript,
    ShotVideo,
)
from short_drama.schemas.generations.batches.batch import BatchPreflight
from short_drama.service.creation.storyboard.videos.video_context import (
    DEFAULT_VIDEO_SETTINGS,
    video_context_hash,
)
from short_drama.service.generations.admission.context import GenerationContextService
from short_drama.service.generations.batches.base import GenerationBatchServiceBase, digest


class PreflightOperations(GenerationBatchServiceBase):
    def _sources(self, request):
        scope = request.scope
        if scope.project_id:
            self._require(Project, scope.project_id)
        if request.scene == "asset_image":
            query = select(Asset).where(
                Asset.project_id == int(scope.project_id), Asset.deleted_at.is_(None)
            )
            if scope.asset_kind:
                query = query.where(Asset.kind == scope.asset_kind)
            if scope.search:
                query = query.where(
                    or_(
                        Asset.name.contains(scope.search, autoescape=True),
                        Asset.description.contains(scope.search, autoescape=True),
                        Asset.label.contains(scope.search, autoescape=True),
                        cast(Asset.tags, String).contains(scope.search, autoescape=True),
                    )
                )
            model = Asset
        else:
            model = ShotScript
            query = select(model).where(
                model.project_id == int(scope.project_id), model.deleted_at.is_(None)
            )
            if scope.search:
                query = query.where(model.script.contains(scope.search, autoescape=True))
        if request.source_ids is not None:
            query = query.where(model.id.in_(map(int, request.source_ids)))
        rows = list(
            self.session.scalars(
                query.order_by(model.id)
                .limit(101)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if len(rows) > 100:
            raise WorkflowError("batch_limit", "单批最多100项，请缩小选择范围", 422)
        if request.source_ids is not None and len(rows) != len(request.source_ids):
            raise Conflict("选择项已删除或不属于当前筛选范围，请重新选择")
        return rows

    def _request(self, request, row):
        if request.scene == "asset_image":
            parameters = request.asset_parameters.model_dump(exclude_none=True)
            parameters["count"] = request.count
            return {
                "config_id": request.config_id,
                "parameters": parameters,
                "input": {"prompt": "", "reference_media_ids": []},
                "source": {
                    "scene": request.scene,
                    "asset_id": str(row.id),
                    "row_version": str(row.row_version),
                    **{
                        k: v
                        for k, v in request.scope.model_dump().items()
                        if k in {"project_id"} and v
                    },
                },
            }
        creation, shot, _, _, context_hash = GenerationContextService(
            self.session
        ).locked_shot_context(row.id)
        source = {
            "scene": request.scene,
            "shot_id": str(row.id),
            "row_version": str(row.row_version),
        }
        if request.scene == "shot_video":
            image = self.session.scalar(select(ShotImage).where(ShotImage.shot_id == row.id))
            if image is None:
                raise WorkflowError("video_reference_required", "请先采用分镜图片", 422)
            settings = shot.video_settings or DEFAULT_VIDEO_SETTINGS
            source.update(
                reference_media_id=str(image.media_id),
                context_hash=video_context_hash(
                    context_hash,
                    image.media_id,
                    shot.video_prompt,
                    settings,
                    session=self.session,
                    shot=shot,
                ),
            )
            return {"config_id": request.config_id, "parameters": {}, "source": source}
        settings = shot.image_settings or {
            "aspect": None,
            "resolution": "2K",
            "layout": "single",
        }
        source.update(context_mode="saved", context_hash=context_hash, layout=settings["layout"])
        return {
            "config_id": request.config_id,
            "source": source,
            "input": {"prompt": "", "reference_media_ids": []},
            "parameters": {
                "count": request.count,
                "resolution": settings["resolution"],
                "aspect": settings["aspect"],
            },
        }

    def _availability(self, request, row):
        conditions = source_conditions(
            AIGenerationRecord, {"source_scene": request.scene, "source_id": str(row.id)}
        )
        active = self.session.scalar(
            select(AsyncTask.id)
            .join(AIGenerationRecord)
            .where(*conditions, AsyncTask.status.in_(("queued", "running")))
            .limit(1)
        )
        if active:
            return "active", "已有排队或运行中的任务", str(active)
        unknown = self.session.scalar(
            select(AsyncTask.id)
            .join(AIGenerationRecord)
            .where(
                *conditions,
                AsyncTask.status == "failed",
                or_(
                    AIGenerationRecord.status.in_(("sent", "unknown")),
                    AsyncTask.error["code"].as_string() == "message_delivery_unknown",
                ),
            )
            .limit(1)
        )
        if unknown:
            return "needs_review", "已有受理结果待核对的任务，不可批量重发", str(unknown)
        if request.mode == "regenerate":
            return "ready", "", None
        if request.scene == "asset_image":
            adopted = row.media_id
            candidate = self.session.scalar(
                select(AssetImageCandidate.id)
                .where(AssetImageCandidate.asset_id == row.id)
                .limit(1)
            )
        else:
            model = ShotImage if request.scene == "shot_image" else ShotVideo
            adopted = self.session.scalar(select(model.id).where(model.shot_id == row.id).limit(1))
            candidate = self.session.scalar(
                select(MediaAsset.id).join(AIGenerationRecord).where(*conditions).limit(1)
            )
        if adopted:
            return "adopted", "已有采用结果；如需重新生成请选择明确重生成", None
        if candidate:
            return "review", "已有候选，请先审核", None
        return "ready", "", None

    def _preflight(self, request):
        if self.session.info.get("actor") and request.scope.project_id:
            from short_drama.db.access.identity import set_scope
            from short_drama.db.scope.resource_scope import require_project

            require_project(self.session, request.scope.project_id)
            set_scope(self.session, (None, request.scope.project_id))
        config = self.generations._config(
            "video" if request.scene == "shot_video" else "image", request.config_id
        )
        kind = config.service_type
        snapshot = {
            k: getattr(config, k)
            for k in (
                "base_url",
                "model_key",
                "provider",
                "service_type",
                "name",
                "capability_cache",
            )
        }
        items, prepared = [], {}
        for row in self._sources(request):
            state, reason, task_id = self._availability(request, row)
            item = {
                "source_id": str(row.id),
                "name": row.name if request.scene == "asset_image" else f"镜头 {row.position}",
                "state": state,
                "reason": reason,
                "task_id": task_id,
                "row_version": str(row.row_version),
            }
            # Validate all sources, including excluded ones, so the preflight digest
            # also changes when dependency context changes without a row-version bump.
            try:
                payload = self.generations._prepare(kind, self._request(request, row))
                validate_request(snapshot, payload)
                item["context_hash"] = digest(payload)
                item["parameters"] = payload["parameters"]
                if state == "ready":
                    prepared[str(row.id)] = payload
            except (BusinessError, GenerationError) as error:
                item.update(
                    state="blocked",
                    reason=error.message
                    if isinstance(error, BusinessError)
                    else GenerationRequestError(error.code).message,
                )
            items.append(item)
        result = {
            "items": items,
            "config_version": str(config.row_version),
            "task_count": len(prepared),
            "output_count": len(prepared) * request.count,
            "concurrency": getattr(self.settings, f"generation_batch_{kind}_concurrency"),
        }
        result["preflight_hash"] = digest(
            {"request": request.model_dump(mode="json"), "result": result}
        )
        return result, prepared, config

    def preflight(self, payload):
        self._enabled()
        request = BatchPreflight.model_validate(payload)
        with self._transaction():
            result, _, _ = self._preflight(request)
            return result
