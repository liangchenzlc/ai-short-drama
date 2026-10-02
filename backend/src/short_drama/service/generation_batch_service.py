"""Preflight and durable batches; all model calls use existing single-task workers."""

import copy
import hashlib
import json
from collections import Counter

from sqlalchemy import String, cast, func, or_, select

from short_drama.ai import GenerationError, validate_request
from short_drama.core.exceptions import (
    BusinessError,
    Conflict,
    GenerationRequestError,
    WorkflowError,
)
from short_drama.dao.async_task_dao import source_conditions
from short_drama.dao.task_runtime_dao import finish, latest_record
from short_drama.db.access import scoped_key
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    Asset,
    AssetImageCandidate,
    AsyncTask,
    Episode,
    EpisodeAsset,
    GenerationBatchItem,
    GenerationBatchJob,
    GlobalAsset,
    MediaAsset,
    MediaFile,
    ProjectAsset,
    ShotImage,
    ShotScript,
    ShotVideo,
)
from short_drama.schemas.generation_batch import BatchCreate, BatchPreflight, BatchRetry
from short_drama.utils.snowflake import next_id

from .ai_generation_service import AIGenerationService, can_retry, resume_action
from .base import BaseService, utcnow
from .generation_context_service import GenerationContextService
from .shot_video_context import DEFAULT_VIDEO_SETTINGS, video_context_hash


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def item_status(task, record):
    if record.status in {"unknown", "sent"} and task.status == "failed":
        return "needs_review"
    if (task.error or {}).get("code") in {
        "provider_acceptance_unknown",
        "acceptance_unknown",
        "message_delivery_unknown",
    }:
        return "needs_review"
    return task.status if task.status in {"succeeded", "failed", "cancelled"} else "active"


def batch_status(states, control):
    counts = Counter(states)
    if counts["needs_review"]:
        return "needs_review"
    if counts["waiting"] or counts["active"] or counts["blocked"]:
        return control if control in {"paused", "cancelled", "needs_review"} else "running"
    if counts["succeeded"] == len(states):
        return "succeeded"
    if counts["succeeded"]:
        return "partial"
    if counts["failed"]:
        return "failed"
    return "cancelled"


class GenerationBatchService(BaseService):
    model = GenerationBatchJob

    def __init__(self, session, settings):
        super().__init__(session)
        self.settings = settings
        self.generations = AIGenerationService(session, settings)

    def _enabled(self):
        if not self.settings.generation_batches_enabled:
            raise WorkflowError("batch_disabled", "批量生成尚未启用", 503)

    def _sources(self, request):
        scope = request.scope
        if scope.episode_id:
            episode = self._require(Episode, scope.episode_id)
            if episode.project_id != scope.project_id:
                raise BusinessError("分集不属于当前项目")
        if request.scene == "asset_image":
            library = {"global": GlobalAsset, "project": ProjectAsset, "episode": EpisodeAsset}[
                scope.library
            ]
            query = select(Asset).join(library, library.asset_id == Asset.id)
            if scope.library == "episode":
                query = query.where(library.episode_id == int(scope.episode_id))
            elif scope.library == "project":
                query = query.where(library.project_id == int(scope.project_id))
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
                model.episode_id == int(scope.episode_id), model.deleted_at.is_(None)
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
                        if k in {"project_id", "episode_id"} and v
                    },
                },
            }
        episode, shot, _, _, context_hash = GenerationContextService(
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
            "aspect": "inherit",
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
                "aspect": episode.aspect if settings["aspect"] == "inherit" else settings["aspect"],
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
            from short_drama.db.access import require_project, set_scope

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

    def create(self, payload, key):
        try:
            return self._create(payload, key)
        except Conflict:
            fingerprint = digest(BatchCreate.model_validate(payload).model_dump(mode="json"))
            with self._transaction():
                previous = self.session.scalar(
                    select(GenerationBatchJob).where(GenerationBatchJob.idempotency_key == key)
                )
                if previous and previous.request_hash == fingerprint:
                    return self._read_batch(previous), False
            raise

    def _create(self, payload, key):
        self._enabled()
        request = BatchCreate.model_validate(payload)
        from short_drama.db.access import scoped_key

        key = scoped_key(self.session, self.generations._key(key))
        fingerprint = digest(request.model_dump(mode="json"))
        with self._transaction():
            previous = self.session.scalar(
                select(GenerationBatchJob).where(GenerationBatchJob.idempotency_key == key)
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise Conflict("此请求标识已用于其他批次")
                return self._read_batch(previous), False
            base = BatchPreflight.model_validate(
                request.model_dump(exclude={"preflight_hash", "accepted_ids"})
            )
            preview, prepared, config = self._preflight(base)
            if preview["preflight_hash"] != request.preflight_hash:
                raise WorkflowError(
                    "batch_preflight_changed", "预检后来源或模型已变化，请重新预检", 409
                )
            if (
                len(set(request.accepted_ids)) != len(request.accepted_ids)
                or not set(map(str, request.accepted_ids)) <= prepared.keys()
            ):
                raise Conflict("只能提交预检通过且不重复的条目")
            now = utcnow()
            from short_drama.db.access import set_scope

            actor = self.session.info.get("actor")
            if actor:
                project_id = base.scope.project_id if hasattr(base.scope, "project_id") else None
                if getattr(base.scope, "episode_id", None):
                    project_id = self._require(Episode, base.scope.episode_id).project_id
                set_scope(
                    self.session, (None, int(project_id)) if project_id else (actor.user_id, None)
                )
            batch = GenerationBatchJob(
                id=next_id(),
                scene=request.scene,
                config_id=config.id,
                config_version=config.row_version,
                scope=base.model_dump(mode="json"),
                status="running",
                idempotency_key=key,
                request_hash=fingerprint,
                created_at=now,
                updated_at=now,
            )
            self.session.add(batch)
            self.session.flush()
            names = {item["source_id"]: item["name"] for item in preview["items"]}
            for source_id in sorted(map(str, request.accepted_ids), key=int):
                prepared[source_id]["batch_id"] = str(batch.id)
                child, _ = self.generations._insert(
                    config.service_type,
                    prepared[source_id],
                    f"batch:{batch.id}:{source_id}",
                    digest(prepared[source_id]),
                    config,
                )
                task = self.session.get(AsyncTask, int(child["generation_id"]))
                # Prepared and frozen, but not eligible for publication until a
                # database-controlled batch slot has been reserved.
                task.message_status = "idle"
                task.next_run_at = None
                self.session.add(
                    GenerationBatchItem(
                        id=next_id(),
                        batch_id=batch.id,
                        source_id=int(source_id),
                        name=names[source_id],
                        task_id=task.id,
                        status="waiting",
                    )
                )
            self.session.flush()
            return self._read_batch(batch), True

    def _items(self, batch, lock=False):
        query = (
            select(GenerationBatchItem)
            .where(GenerationBatchItem.batch_id == batch.id)
            .order_by(GenerationBatchItem.id)
        )
        query = query.execution_options(populate_existing=True)
        return list(self.session.scalars(query.with_for_update() if lock else query))

    def _reconcile(self, batch):
        items = self._items(batch, lock=True)
        for item in items:
            task = self._require(AsyncTask, item.task_id, for_update=False)
            record = latest_record(self.session, task.id)
            if item.status not in {"waiting", "blocked"} or task.status in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                item.status = item_status(task, record)
        batch.status = batch_status([item.status for item in items], batch.status)
        batch.updated_at = utcnow()
        self.session.flush()
        return items

    def _read_batch(self, batch):
        items = self._items(batch)
        actor = self.session.info.get("actor")
        own_batch = actor is None or batch.initiated_by == actor.user_id
        can_cancel = own_batch
        if actor and not own_batch and batch.project_id:
            from short_drama.domain import Project

            can_cancel = (
                self.session.scalar(
                    select(Project.owner_user_id).where(Project.id == batch.project_id)
                )
                == actor.user_id
            )
        return {
            "id": str(batch.id),
            "scene": batch.scene,
            "config_id": str(batch.config_id),
            "scope": batch.scope,
            "status": batch.status,
            "can_control": own_batch,
            "can_cancel": can_cancel,
            "initiated_by": str(batch.initiated_by) if batch.initiated_by else None,
            "counts": dict(Counter(item.status for item in items)),
            "total": len(items),
            "created_at": batch.created_at,
            "updated_at": batch.updated_at,
        }

    def detail(self, identifier, offset=0, limit=20):
        self._enabled()
        with self._transaction():
            batch = self._require(GenerationBatchJob, identifier)
            items = self._reconcile(batch)
            result = self._read_batch(batch)
            result["items"] = []
            for item in items[offset : offset + limit]:
                task = self.session.get(AsyncTask, item.task_id)
                record = latest_record(self.session, task.id)
                result["items"].append(
                    {
                        "id": str(item.id),
                        "source_id": str(item.source_id),
                        "name": item.name,
                        "task_id": str(item.task_id),
                        "status": item.status,
                        "error": item.error,
                        "task": {
                            **self.generations._summary(task, record),
                            "can_retry": (
                                not self.session.info.get("actor")
                                or task.initiated_by == self.session.info["actor"].user_id
                            )
                            and can_retry(task, record),
                        },
                    }
                )
            result.update(offset=offset, limit=limit)
            return result

    def list(self, offset=0, limit=20):
        self._enabled()
        with self._transaction():
            total = self.session.scalar(select(func.count()).select_from(GenerationBatchJob))
            rows = self.session.scalars(
                select(GenerationBatchJob)
                .order_by(GenerationBatchJob.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return {
                "items": [self._read_batch(row) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def control(self, identifier, action):
        self._enabled()
        with self._transaction():
            config_id = self.session.scalar(
                select(GenerationBatchJob.config_id).where(GenerationBatchJob.id == int(identifier))
            )
            if config_id and action != "cancel":
                self._require(AIModelConfig, config_id)
            batch = self._require(GenerationBatchJob, identifier)
            actor = self.session.info.get("actor")
            if actor and batch.initiated_by != actor.user_id:
                if action != "cancel" or not batch.project_id:
                    raise WorkflowError(
                        "task_actor_required", "Only the batch initiator can do this", 403
                    )
                from short_drama.db.access import require_project

                require_project(self.session, batch.project_id, owner=True)
            items = self._reconcile(batch)
            if batch.status in {"succeeded", "partial", "failed", "cancelled"}:
                return self._read_batch(batch)
            if action == "resume":
                if any(i.status == "needs_review" for i in items):
                    raise Conflict("受理状态尚未核实，请先处理对应子任务")
                config = self.generations._config(
                    "video" if batch.scene == "shot_video" else "image", batch.config_id
                )
                if config.row_version != batch.config_version:
                    raise Conflict("模型配置已变化，请取消未执行项并重新预检")
                for item in items:
                    if item.status == "blocked":
                        item.status = "waiting"
                        item.error = None
                batch.status = "running"
            elif action == "pause":
                batch.status = "paused"
            elif action == "cancel":
                for item in items:
                    task = self._require(AsyncTask, item.task_id)
                    record = latest_record(self.session, task.id)
                    if task.status in {"queued", "running"}:
                        task.cancel_requested = 1
                        if task.status == "queued" and record.status == "prepared":
                            finish(task, "cancelled")
                            item.status = "cancelled"
                batch.status = "cancelled"
            else:
                raise BusinessError("不支持的批次操作")
            batch.updated_at = utcnow()
            self.session.flush()
            return self._read_batch(batch)

    def retry_failed(self, identifier, payload, key):
        try:
            return self._retry_failed(identifier, payload, key)
        except Conflict:
            request = BatchRetry.model_validate(payload)
            fingerprint = digest(
                {"retry_of": str(identifier), "item_ids": sorted(request.item_ids)}
            )
            with self._transaction():
                previous = self.session.scalar(
                    select(GenerationBatchJob).where(
                        GenerationBatchJob.idempotency_key
                        == scoped_key(self.session, self.generations._key(key))
                    )
                )
                if previous and previous.request_hash == fingerprint:
                    return self._read_batch(previous)
            raise

    def _retry_failed(self, identifier, payload, key):
        self._enabled()
        request = BatchRetry.model_validate(payload)
        from short_drama.db.access import scoped_key

        key = scoped_key(self.session, self.generations._key(key))
        fingerprint = digest({"retry_of": str(identifier), "item_ids": sorted(request.item_ids)})
        with self._transaction():
            config_id = self.session.scalar(
                select(GenerationBatchJob.config_id).where(GenerationBatchJob.id == int(identifier))
            )
            if config_id:
                self._require(AIModelConfig, config_id)
            previous = self.session.scalar(
                select(GenerationBatchJob).where(GenerationBatchJob.idempotency_key == key)
            )
            if previous:
                if previous.request_hash != fingerprint:
                    raise Conflict("请求标识已用于其他批次")
                return self._read_batch(previous)
            original = self._require(GenerationBatchJob, identifier)
            actor = self.session.info.get("actor")
            if actor and original.initiated_by != actor.user_id:
                raise WorkflowError(
                    "task_actor_required", "Only the batch initiator can retry", 403
                )
            if actor:
                from short_drama.db.access import scope_of, set_scope

                set_scope(self.session, scope_of(self.session, original))
            config = self.generations._config(
                "video" if original.scene == "shot_video" else "image", original.config_id
            )
            if config.row_version != original.config_version:
                raise Conflict("模型已变化，请重新预检")
            self._reconcile(original)
            selected = [
                item for item in self._items(original, lock=True) if item.id in request.item_ids
            ]
            if len(selected) != len(request.item_ids):
                raise Conflict("重试项重复或不属于此批次")
            children = []
            for item in selected:
                task = self._require(AsyncTask, item.task_id)
                record = latest_record(self.session, task.id)
                if not can_retry(task, record) or resume_action(task, record):
                    raise Conflict("所选任务不可重新生成，或应先安全恢复保存")
                active = self.session.scalar(
                    select(GenerationBatchItem.id)
                    .join(GenerationBatchJob)
                    .where(
                        GenerationBatchJob.scene == original.scene,
                        GenerationBatchItem.source_id == item.source_id,
                        GenerationBatchItem.status.in_(
                            ("waiting", "active", "blocked", "needs_review")
                        ),
                    )
                    .limit(1)
                )
                if active:
                    raise Conflict("来源已有活动批次，请先核对")
                children.append((item, task, record))
            now = utcnow()
            batch = GenerationBatchJob(
                id=next_id(),
                scene=original.scene,
                config_id=config.id,
                config_version=config.row_version,
                scope=copy.deepcopy(original.scope),
                status="running",
                idempotency_key=key,
                request_hash=fingerprint,
                retry_of_id=original.id,
                created_at=now,
                updated_at=now,
            )
            self.session.add(batch)
            self.session.flush()
            for item, original_task, record in children:
                payload = copy.deepcopy(record.request_data)
                payload["batch_id"] = str(batch.id)
                child, _ = self.generations._insert(
                    config.service_type,
                    payload,
                    f"batch:{batch.id}:{item.source_id}",
                    digest(payload),
                    config,
                    retry_of_id=original_task.id,
                )
                task = self.session.get(AsyncTask, int(child["generation_id"]))
                task.message_status = "idle"
                task.next_run_at = None
                self.session.add(
                    GenerationBatchItem(
                        id=next_id(),
                        batch_id=batch.id,
                        source_id=item.source_id,
                        name=item.name,
                        task_id=task.id,
                        status="waiting",
                    )
                )
            self.session.flush()
            return self._read_batch(batch)


def dispatch_batches(factory, settings):
    """One transaction per model serializes slots across schedulers and batches."""
    if not settings.generation_batches_enabled:
        return 0
    dispatched = 0
    with factory() as session:
        configs = session.scalars(
            select(GenerationBatchJob.config_id)
            .where(
                GenerationBatchJob.status.in_(("running", "paused", "needs_review", "cancelled"))
            )
            .distinct()
        ).all()
    for config_id in configs:
        with factory.begin() as session:
            from short_drama.domain import Project

            # API transactions and revocation start with the project lock.
            project_ids = session.scalars(
                select(GenerationBatchJob.project_id)
                .where(
                    GenerationBatchJob.config_id == config_id,
                    GenerationBatchJob.project_id.is_not(None),
                )
                .distinct()
            ).all()
            for project_id in sorted(project_ids):
                session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            config = session.scalar(
                select(AIModelConfig).where(AIModelConfig.id == config_id).with_for_update()
            )
            if config is None:
                continue
            svc = GenerationBatchService(session, settings)
            batches = list(
                session.scalars(
                    select(GenerationBatchJob)
                    .where(
                        GenerationBatchJob.config_id == config_id,
                        GenerationBatchJob.status.in_(
                            ("running", "paused", "needs_review", "cancelled")
                        ),
                    )
                    .order_by(GenerationBatchJob.id)
                    .with_for_update()
                )
            )
            groups = [(batch, svc._reconcile(batch)) for batch in batches]
            active = sum(
                item.status in {"active", "needs_review"} for _, items in groups for item in items
            )
            cap = getattr(settings, f"generation_batch_{config.service_type}_concurrency")
            for batch, items in groups:
                if batch.status != "running":
                    continue
                for item in items:
                    if item.status != "waiting" or active >= cap:
                        continue
                    task = svc._require(AsyncTask, item.task_id)
                    record = latest_record(session, task.id)
                    from short_drama.service.task_access import may_submit

                    if not may_submit(session, task):
                        finish(task, "cancelled", {"code": "access_revoked"})
                        item.status = "cancelled"
                        continue
                    references = (record.request_data.get("input") or {}).get(
                        "reference_media_ids", []
                    )
                    inputs = record.request_data.get("input") or {}
                    references = references + inputs.get("audio_reference_media_ids", [])
                    references = references + [
                        inputs[k]
                        for k in ("first_frame_media_id", "last_frame_media_id")
                        if inputs.get(k)
                    ]
                    available = all(session.get(MediaFile, int(i)) is not None for i in references)
                    source = session.get(
                        Asset if batch.scene == "asset_image" else ShotScript, item.source_id
                    )
                    native_changed = False
                    if source is not None and batch.scene == "shot_video":
                        from .native_voice_service import native_context

                        frozen = (record.request_data.get("source_snapshot") or {}).get(
                            "native_speech"
                        )
                        native_changed = frozen != native_context(
                            session, source, settings=settings
                        )
                    if (
                        not config.enabled
                        or config.is_deleted
                        or config.row_version != batch.config_version
                        or not available
                        or native_changed
                        or source is None
                        or getattr(source, "deleted_at", None)
                        or str(source.row_version)
                        != str(record.request_data.get("source", {}).get("row_version"))
                    ):
                        item.status = "blocked"
                        item.error = {
                            "code": "batch_dependency_unavailable",
                            "message": "模型、来源或参考媒体已变化，请核对后恢复或重新预检",
                        }
                        batch.status = "paused"
                        break
                    task.message_status = "pending"
                    task.next_run_at = task.updated_at = utcnow()
                    item.status = "active"
                    active += 1
                    dispatched += 1
    return dispatched
