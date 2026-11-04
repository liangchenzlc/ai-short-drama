"""LangGraph typed serializer adapter; every I/O owns its short Session."""

import asyncio
import logging
from collections.abc import Iterator, Sequence
from dataclasses import fields, is_dataclass
from hashlib import sha256

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from short_drama.agent.resources.contracts import assert_reference_payload
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.service.assistant.execution.graph import AgentGraphService

logger = logging.getLogger(__name__)
MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024


def namespace_key(namespace: str) -> bytes:
    return sha256(namespace.encode("utf-8")).digest()


def _validate_state(value):
    if isinstance(value, BaseModel):
        _validate_state(value.model_dump())
    elif is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _validate_state(getattr(value, field.name))
    elif isinstance(value, dict):
        assert_reference_payload(value)
        for child in value.values():
            _validate_state(child)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            _validate_state(child)
    else:
        assert_reference_payload(value)


class MySQLCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, service: AgentGraphService):
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self.service = service
        self.thread_id = f"owner:{service.owner_id}:run:{service.run_id}"

    def _config(self, checkpoint_id=None, *, namespace=""):
        return {
            "configurable": {
                "thread_id": self.thread_id,
                "checkpoint_ns": namespace,
                **({"checkpoint_id": checkpoint_id} if checkpoint_id else {}),
            }
        }

    def _check(self, config):
        values = config.get("configurable", {})
        if values.get("thread_id") != self.thread_id:
            raise Conflict("执行图不能跨账号或 Run 访问")
        return values

    def _encode(self, value):
        _validate_state(value)
        codec, payload = self.serde.dumps_typed(value)
        if len(payload) > MAX_CHECKPOINT_BYTES:
            raise WorkflowError("agent_checkpoint_too_large", "Agent 执行状态超过存储上限", 500)
        return codec, payload

    def _decode(self, codec, payload):
        try:
            return self.serde.loads_typed((codec, payload))
        except Exception as error:
            code = (
                "agent_checkpoint_codec_unsupported"
                if codec not in {"null", "bytes", "bytearray", "json", "msgpack"}
                else "agent_checkpoint_corrupt"
            )
            logger.exception(code, extra={"run_id": self.service.run_id})
            raise WorkflowError(
                code, "执行图数据无法读取，请新建任务并联系维护人员排查", 409
            ) from error

    def _tuple(self, row):
        namespace = row["namespace"]
        return CheckpointTuple(
            config=self._config(row["checkpoint_id"], namespace=namespace),
            checkpoint=self._decode(row["codec"], row["payload"]),
            metadata=row["metadata"],
            parent_config=self._config(row["parent_id"], namespace=namespace)
            if row["parent_id"]
            else None,
            pending_writes=[
                (key, channel, self._decode(codec, payload))
                for key, channel, codec, payload in row["writes"]
            ],
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        values = self._check(config)
        rows = self.service.load(
            namespace=values.get("checkpoint_ns", ""),
            checkpoint_id=values.get("checkpoint_id"),
            limit=1,
        )
        return self._tuple(rows[0]) if rows else None

    def list(
        self, config: RunnableConfig | None, *, filter=None, before=None, limit=None
    ) -> Iterator[CheckpointTuple]:
        values = self._check(config) if config else {}
        before_values = self._check(before) if before else {}
        for row in self.service.load(
            namespace=values.get("checkpoint_ns"),
            checkpoint_id=values.get("checkpoint_id"),
            before=before_values.get("checkpoint_id"),
            limit=limit,
            metadata_filter=filter,
        ):
            yield self._tuple(row)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        values = self._check(config)
        namespace = values.get("checkpoint_ns", "")
        codec, payload = self._encode(checkpoint)
        self.service.put(
            namespace=namespace,
            namespace_hash=namespace_key(namespace),
            checkpoint_id=checkpoint["id"],
            parent_id=values.get("checkpoint_id"),
            codec=codec,
            payload=payload,
            metadata=metadata,
        )
        return {
            "configurable": {
                **values,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, object]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        values = self._check(config)
        checkpoint_id = values.get("checkpoint_id")
        if checkpoint_id is None:
            raise Conflict("节点写入必须归属真实检查点")
        encoded = []
        for index, (channel, value) in enumerate(writes):
            if isinstance(value, BaseException):
                # 错误仍留在框架错误通道，不伪装成成功工具结果，也不保存供应商原文。
                if channel != "__error__":
                    raise Conflict("异常只能写入执行图错误通道")
                value = WorkflowError("agent_graph_node_failed", "agent_graph_node_failed", 500)
            codec, payload = self._encode(value)
            encoded.append((WRITES_IDX_MAP.get(channel, index), channel, codec, payload))
        namespace = values.get("checkpoint_ns", "")
        from short_drama.agent.runtime.waiting import persistence_facts

        interrupts, completed_operations = persistence_facts(writes)
        self.service.put_writes(
            namespace=namespace,
            namespace_hash=namespace_key(namespace),
            checkpoint_id=checkpoint_id,
            writes=encoded,
            task_key=task_id,
            task_path=task_path,
            interrupts=interrupts,
            completed_operations=completed_operations,
        )

    def delete_thread(self, thread_id: str) -> None:
        self._check({"configurable": {"thread_id": thread_id}})
        self.service.delete_thread(namespace=None)

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

    async def adelete_thread(self, thread_id):
        await asyncio.to_thread(self.delete_thread, thread_id)
