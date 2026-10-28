"""批次暂停、取消与失败项重试。"""

import copy

from sqlalchemy import select

from short_drama.core.exceptions import (
    BusinessError,
    Conflict,
    WorkflowError,
)
from short_drama.dao.generations.runtime.lifecycle import latest_record
from short_drama.db.access.identity import scoped_key
from short_drama.domain import (
    AIModelConfig,
    AsyncTask,
    GenerationBatchItem,
    GenerationBatchJob,
)
from short_drama.schemas.generations.batches.batch import BatchRetry
from short_drama.service.base import utcnow
from short_drama.service.generations.admission.task_policy import can_retry, resume_action
from short_drama.service.generations.batches.base import (
    GenerationBatchServiceBase,
    digest,
)
from short_drama.service.generations.runtime.lifecycle import finish
from short_drama.utils.snowflake import next_id


class ControlsOperations(GenerationBatchServiceBase):
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
                from short_drama.db.scope.resource_scope import require_project

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
        from short_drama.db.access.identity import scoped_key

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
                from short_drama.db.access.identity import set_scope
                from short_drama.db.scope.resource_scope import scope_of

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
