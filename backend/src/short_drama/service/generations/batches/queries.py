"""批次及子任务状态投影与对账。"""

from collections import Counter

from sqlalchemy import func, select

from short_drama.dao.generations.runtime.lifecycle import latest_record
from short_drama.domain import (
    AsyncTask,
    GenerationBatchItem,
    GenerationBatchJob,
)
from short_drama.service.base import utcnow
from short_drama.service.generations.admission.task_policy import can_retry
from short_drama.service.generations.batches.base import (
    GenerationBatchServiceBase,
    batch_status,
    item_status,
)


class QueriesOperations(GenerationBatchServiceBase):
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
