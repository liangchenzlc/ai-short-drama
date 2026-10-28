"""Transaction and immutable-version boundaries for the MySQL graph saver."""

import json

from sqlalchemy import select

from short_drama.agent.core.access import lock_run
from short_drama.agent.resources.contracts import reference_json
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.dao.agent_graph_dao import AgentGraphDAO
from short_drama.dao.agent_task_dao import AgentTaskDAO
from short_drama.domain.agent import AgentRun
from short_drama.domain.agent_graph import AgentGraphCheckpoint, AgentGraphWrite
from short_drama.utils.snowflake import next_id


class AgentGraphService:
    def __init__(self, factory, *, owner: int, task: int, run: int, namespace: str, version: str):
        self.factory = factory
        self.owner, self.task, self.run = owner, task, run
        self.namespace, self.version = namespace, version

    def _dao(self, session):
        return AgentGraphDAO(session, self.owner, self.task, self.run, self.namespace)

    def _bound(self, session, *, lock=False):
        # Graph writes and business settlement share the same lock order.
        # Locking Run alone then inserting Task FKs deadlocks with tool claims.
        locked = lock_run(session, self.run) if lock else None
        task = AgentTaskDAO(session).task(self.task)
        query = select(AgentRun).where(AgentRun.id == self.run)
        run = locked[2] if locked else None
        if not lock:
            run = session.scalar(query)
        if (
            task is None
            or run is None
            or run.task_id != task.id
            or run.initiated_by != self.owner
            or run.conversation_id != task.conversation_id
            or (run.checkpoint or {}).get("engine") != self.version
        ):
            raise NotFound("任务执行图不存在或版本不匹配")

    def load(self, checkpoint_id=None, *, before=None, limit=100, metadata_filter=None):
        with self.factory.begin() as session:
            self._bound(session)
            result = []
            for row in self._dao(session).checkpoints(
                checkpoint_id, before=before, limit=limit, metadata_filter=metadata_filter
            ):
                if row.graph_version != self.version:
                    raise Conflict("执行图版本不能在任务中途切换")
                result.append(
                    {
                        "checkpoint": row.checkpoint,
                        "metadata": row.checkpoint_metadata,
                        "parent_id": row.parent_id,
                        "writes": [
                            (entry.task_key, entry.channel, entry.payload["value"])
                            for entry in self._dao(session).writes(row.checkpoint_id)
                        ],
                    }
                )
            return result

    def put(self, checkpoint, metadata, parent_id):
        checkpoint = json.loads(reference_json(checkpoint))
        metadata = json.loads(reference_json(metadata))
        with self.factory.begin() as session:
            self._bound(session, lock=True)
            dao = self._dao(session)
            # MySQL REPEATABLE READ may have taken a snapshot before waiting on
            # the Run lock. Use a current read when checking an idempotent write.
            existing = dao.checkpoints(checkpoint["id"], limit=1, lock=True)
            if existing:
                if (
                    existing[0].checkpoint != checkpoint
                    or existing[0].checkpoint_metadata != metadata
                ):
                    raise Conflict("同一执行图检查点不能覆盖为不同内容")
                return
            session.add(
                AgentGraphCheckpoint(
                    id=next_id(),
                    owner_user_id=self.owner,
                    task_id=self.task,
                    run_id=self.run,
                    namespace=self.namespace,
                    checkpoint_id=checkpoint["id"],
                    parent_id=parent_id,
                    graph_version=self.version,
                    checkpoint=checkpoint,
                    checkpoint_metadata=metadata,
                )
            )

    def put_writes(self, checkpoint_id, writes, task_key, task_path, indexes):
        payloads = [json.loads(reference_json({"value": value})) for _, value in writes]
        with self.factory.begin() as session:
            self._bound(session, lock=True)
            dao = self._dao(session)
            for (channel, _), payload, index in zip(writes, payloads, indexes, strict=True):
                row = dao.write(checkpoint_id, task_key, index)
                if row is not None:
                    if index < 0:
                        row.payload, row.channel = payload, channel
                    continue
                session.add(
                    AgentGraphWrite(
                        id=next_id(),
                        owner_user_id=self.owner,
                        task_id=self.task,
                        run_id=self.run,
                        namespace=self.namespace,
                        checkpoint_id=checkpoint_id,
                        task_key=task_key,
                        task_path=task_path,
                        write_index=index,
                        channel=channel,
                        payload=payload,
                    )
                )
