"""批量任务的幂等受理与持久化。"""

from sqlalchemy import select

from short_drama.core.exceptions import (
    Conflict,
    WorkflowError,
)
from short_drama.db.access.identity import scoped_key
from short_drama.domain import (
    AsyncTask,
    GenerationBatchItem,
    GenerationBatchJob,
    Project,
)
from short_drama.schemas.generations.batches.batch import BatchCreate, BatchPreflight
from short_drama.service.base import utcnow
from short_drama.service.generations.batches.base import GenerationBatchServiceBase, digest
from short_drama.utils.snowflake import next_id


class AdmissionOperations(GenerationBatchServiceBase):
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
            from short_drama.db.access.identity import set_scope

            actor = self.session.info.get("actor")
            if actor:
                project_id = base.scope.project_id if hasattr(base.scope, "project_id") else None
                if getattr(base.scope, "project_id", None):
                    project_id = self._require(Project, base.scope.project_id).id
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
