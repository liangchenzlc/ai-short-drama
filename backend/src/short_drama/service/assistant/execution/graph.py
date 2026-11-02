"""Short-session persistence bound to one owner, task, Run and execution lease."""

import json
import logging

from short_drama.agent.core.access import LeaseLost, lock_run
from short_drama.agent.core.fence import owns_execution
from short_drama.agent.resources.contracts import reference_json
from short_drama.core.exceptions import Conflict, NotFound
from short_drama.dao.assistant.execution.graph import AgentGraphDAO
from short_drama.domain.assistant.execution.graph import AgentGraphCheckpoint, AgentGraphWrite
from short_drama.utils.snowflake import next_id

logger = logging.getLogger(__name__)


class AgentGraphService:
    def __init__(
        self,
        factory,
        *,
        owner_id: int,
        task_id: int,
        run_id: int,
        version: str,
        fence: tuple[int, str],
    ):
        self.factory = factory
        self.owner_id, self.task_id, self.run_id = owner_id, task_id, run_id
        self.version, self.fence = version, fence

    def _dao(self, session, namespace):
        return AgentGraphDAO(session, self.owner_id, self.task_id, self.run_id, namespace)

    def _bound(self, session):
        # 与业务写入保持 Project -> Conversation -> Task -> Run 锁顺序。
        rows = lock_run(session, self.run_id)
        if rows is None:
            raise NotFound("任务执行图不存在")
        _, conversation, run = rows
        if (
            run.task_id != self.task_id
            or run.initiated_by != self.owner_id
            or conversation.owner_user_id != self.owner_id
        ):
            raise NotFound("任务执行图不存在")
        if (run.checkpoint or {}).get("engine") != self.version:
            logger.error("agent_graph_version_mismatch", extra={"run_id": self.run_id})
            raise Conflict("执行图版本不能在任务中途切换")
        if self.fence[1] is None or not owns_execution(run, self.fence):
            raise LeaseLost()

    def load(
        self,
        *,
        namespace: str | None,
        checkpoint_id=None,
        before=None,
        limit=None,
        metadata_filter=None,
    ):
        with self.factory.begin() as session:
            self._bound(session)
            result = []
            for row in self._dao(session, namespace).checkpoints(
                checkpoint_id,
                before=before,
                limit=limit,
                metadata_filter=metadata_filter,
                lock=True,
            ):
                if row.graph_version != self.version:
                    logger.error("agent_graph_version_mismatch", extra={"run_id": self.run_id})
                    raise Conflict("执行图版本不能在任务中途切换")
                result.append(
                    {
                        "namespace": row.namespace,
                        "checkpoint_id": row.checkpoint_id,
                        "codec": row.codec,
                        "payload": row.payload,
                        "metadata": row.checkpoint_metadata,
                        "parent_id": row.parent_id,
                        "writes": [
                            (entry.task_key, entry.channel, entry.codec, entry.payload)
                            for entry in self._dao(session, row.namespace).writes(row.checkpoint_id)
                        ],
                    }
                )
            return result

    def put(self, *, namespace, namespace_hash, checkpoint_id, parent_id, codec, payload, metadata):
        metadata = json.loads(reference_json(metadata))
        with self.factory.begin() as session:
            self._bound(session)
            dao = self._dao(session, namespace)
            existing = dao.checkpoints(checkpoint_id, limit=1, lock=True)
            if existing:
                row = existing[0]
                if (
                    row.codec != codec
                    or row.payload != payload
                    or row.parent_id != parent_id
                    or row.checkpoint_metadata != metadata
                ):
                    raise Conflict("同一执行图检查点不能覆盖为不同内容")
                return
            session.add(
                AgentGraphCheckpoint(
                    id=next_id(),
                    owner_user_id=self.owner_id,
                    task_id=self.task_id,
                    run_id=self.run_id,
                    namespace=namespace,
                    namespace_hash=namespace_hash,
                    checkpoint_id=checkpoint_id,
                    parent_id=parent_id,
                    graph_version=self.version,
                    codec=codec,
                    payload=payload,
                    checkpoint_metadata=metadata,
                )
            )

    def put_writes(
        self,
        *,
        namespace,
        namespace_hash,
        checkpoint_id,
        writes,
        task_key,
        task_path,
        interrupts=(),
        completed_operations=(),
    ):
        with self.factory.begin() as session:
            self._bound(session)
            dao = self._dao(session, namespace)
            for index, channel, codec, payload in writes:
                row = dao.write(checkpoint_id, task_key, index)
                if row is not None:
                    if index < 0:
                        row.codec, row.payload, row.channel, row.task_path = (
                            codec,
                            payload,
                            channel,
                            task_path,
                        )
                    continue
                session.add(
                    AgentGraphWrite(
                        id=next_id(),
                        owner_user_id=self.owner_id,
                        task_id=self.task_id,
                        run_id=self.run_id,
                        namespace=namespace,
                        namespace_hash=namespace_hash,
                        checkpoint_id=checkpoint_id,
                        task_key=task_key,
                        task_path=task_path,
                        write_index=index,
                        channel=channel,
                        codec=codec,
                        payload=payload,
                    )
                )
                session.flush()
            if interrupts or completed_operations:
                from short_drama.service.assistant.execution.waiting import (
                    persist_interrupts_locked,
                )

                persist_interrupts_locked(
                    session,
                    self.run_id,
                    namespace,
                    checkpoint_id,
                    task_key,
                    interrupts,
                    completed_operations,
                )

    def delete_thread(self, *, namespace: str | None):
        with self.factory.begin() as session:
            self._bound(session)
            self._dao(session, namespace).delete_thread()
