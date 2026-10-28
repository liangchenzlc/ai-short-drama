"""LangGraph saver adapter. Persistence is delegated to the scoped service."""

import asyncio
from collections.abc import Iterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from short_drama.core.exceptions import Conflict
from short_drama.service.agent_graph_service import AgentGraphService


class MySQLCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, service: AgentGraphService):
        super().__init__()
        self.service = service
        # LangGraph owns checkpoint_ns (the root graph always uses ""). The
        # delivery scope belongs to our storage adapter, not to a fake subgraph.
        self.thread_id = f"owner:{service.owner}:task:{service.task}:{service.namespace}"

    def _config(self, checkpoint_id=None):
        return {
            "configurable": {
                "thread_id": self.thread_id,
                "checkpoint_ns": "",
                **({"checkpoint_id": checkpoint_id} if checkpoint_id else {}),
            }
        }

    def _check(self, config):
        values = config.get("configurable", {})
        if values.get("thread_id") != self.thread_id or values.get("checkpoint_ns", "") != "":
            raise Conflict("执行图不能跨账号、任务或投递范围访问")
        return values.get("checkpoint_id")

    def _tuple(self, row):
        return CheckpointTuple(
            config=self._config(row["checkpoint"]["id"]),
            checkpoint=row["checkpoint"],
            metadata=row["metadata"],
            parent_config=self._config(row["parent_id"]) if row["parent_id"] else None,
            pending_writes=row["writes"],
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        rows = self.service.load(self._check(config), limit=1)
        return self._tuple(rows[0]) if rows else None

    def list(
        self, config: RunnableConfig | None, *, filter=None, before=None, limit=None
    ) -> Iterator[CheckpointTuple]:
        checkpoint_id = self._check(config or self._config())
        before_id = self._check(before) if before else None
        for row in self.service.load(
            checkpoint_id, before=before_id, limit=limit or 100, metadata_filter=filter
        ):
            yield self._tuple(row)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        self.service.put(checkpoint, metadata, self._check(config))
        return self._config(checkpoint["id"])

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, object]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        checkpoint_id = self._check(config)
        if checkpoint_id is None:
            raise Conflict("节点写入必须归属真实检查点")
        writes = [
            (
                channel,
                {"code": "agent_graph_node_failed", "type": type(value).__name__}
                if isinstance(value, BaseException)
                else value,
            )
            for channel, value in writes
        ]
        self.service.put_writes(
            checkpoint_id,
            writes,
            task_id,
            task_path,
            [WRITES_IDX_MAP.get(channel, index) for index, (channel, _) in enumerate(writes)],
        )

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for row in rows:
            yield row

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)
