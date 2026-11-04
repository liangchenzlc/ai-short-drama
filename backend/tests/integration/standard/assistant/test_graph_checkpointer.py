"""Typed checkpoint persistence against a disposable real MySQL database."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256
from threading import Event, local
from time import monotonic

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import WRITES_IDX_MAP, empty_checkpoint
from langgraph.types import Interrupt
from sqlalchemy import event, text

from short_drama.agent.core import ENGINE_VERSION
from short_drama.agent.core.access import LeaseLost, lock_run
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.time import utcnow
from short_drama.domain.assistant.execution.graph import AgentGraphWrite
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.service.assistant.execution.graph import AgentGraphService
from short_drama.utils.snowflake import next_id
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
)

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


@pytest.mark.parametrize(
    ("codec", "payload", "code"),
    [
        ("future-codec", b"opaque", "agent_checkpoint_codec_unsupported"),
        ("msgpack", b"\xc1", "agent_checkpoint_corrupt"),
    ],
)
def test_checkpoint_decode_errors_are_classified(task_workspace, codec, payload, code, caplog):
    _, accepted = new_task(task_workspace)
    saver = saver_for(bound(task_workspace, accepted))
    with pytest.raises(WorkflowError) as caught:
        saver._decode(codec, payload)
    assert caught.value.code == code
    assert caplog.records[-1].exc_info is not None


def test_checkpoint_engine_version_fence_has_its_own_code(task_workspace):
    _, accepted = new_task(task_workspace)
    service = bound(task_workspace, accepted)
    saver = saver_for(service)
    config = saver.put(saver._config(), empty_checkpoint(), {}, {})
    with service.factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.checkpoint = {**run.checkpoint, "engine": "obsolete-engine"}
    with pytest.raises(WorkflowError) as caught:
        saver.get_tuple(config)
    assert caught.value.code == "agent_checkpoint_version_mismatch"


def bound(workspace, accepted, *, owner=1, version=1):
    factory, _, _ = workspace
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.message_version = version
        run.lease_token = "a" * 32
        run.lease_until = utcnow() + timedelta(minutes=5)
    return AgentGraphService(
        factory,
        owner_id=owner,
        task_id=accepted.task.id,
        run_id=accepted.run.id,
        version=ENGINE_VERSION,
        fence=(version, "a" * 32),
    )


def saver_for(service):
    from short_drama.agent.runtime.checkpointer import MySQLCheckpointSaver

    return MySQLCheckpointSaver(service)


def test_typed_checkpoint_long_namespace_parent_and_pending_writes(task_workspace):
    _, accepted = new_task(task_workspace)
    service = bound(task_workspace, accepted)
    saver = saver_for(service)
    namespace = "tools:" + "子图" * 4000
    config = saver._config(namespace=namespace)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {
        "messages": [
            AIMessage(content="长" * 40000),
            ToolMessage(content="已保存", tool_call_id="call-1"),
        ]
    }
    first = saver.put(config, checkpoint, {"source": "input", "step": 0}, {})
    second_checkpoint = {**checkpoint, "id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}
    second = saver.put(first, second_checkpoint, {"source": "loop", "step": 1}, {})
    saver.put_writes(second, [("answer", "first")], "worker", "path" * 400)
    saver.put_writes(second, [("answer", "ignored")], "worker")
    saver.put_writes(
        second, [("__interrupt__", (Interrupt(value={"question": "确认"}, id="i"),))], "wait"
    )
    saver.put_writes(second, [("__error__", "first")], "error")
    saver.put_writes(second, [("__error__", "replacement")], "error")
    saver.put_writes(second, [("__error__", RuntimeError("provider-secret"))], "failed")
    restored = saver_for(service).get_tuple(second)
    assert restored.checkpoint == second_checkpoint
    assert restored.parent_config == first
    assert ("worker", "answer", "first") in restored.pending_writes
    assert ("error", "__error__", "replacement") in restored.pending_writes
    assert (
        "failed",
        "__error__",
        "WorkflowError('agent_graph_node_failed')",
    ) in restored.pending_writes
    assert any(
        isinstance(value[0], Interrupt)
        for _, channel, value in restored.pending_writes
        if channel == "__interrupt__"
    )
    assert len(list(saver.list(config, filter={"source": "input"}))) == 1
    assert len(list(saver.list(config, before=second))) == 1
    sibling = saver._config(namespace=namespace + "other")
    saver.put(sibling, checkpoint, {"source": "input"}, {})
    assert len(list(saver.list(None))) == 3
    with pytest.raises(WorkflowError):
        saver.put_writes(
            second,
            [
                (
                    "messages",
                    AIMessage(
                        content=[
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AAAA"},
                            }
                        ]
                    ),
                )
            ],
            "media",
        )


def test_stable_thread_rejects_stale_fence_and_foreign_owner(task_workspace):
    _, accepted = new_task(task_workspace)
    old = saver_for(bound(task_workspace, accepted))
    config = old.put(old._config(), empty_checkpoint(), {}, {})
    new = saver_for(bound(task_workspace, accepted, version=2))
    assert old.thread_id == new.thread_id == f"owner:1:run:{accepted.run.id}"
    assert new.get_tuple(config) is not None
    with pytest.raises(LeaseLost):
        old.get_tuple(config)
    with pytest.raises(Conflict):
        new.get_tuple({"configurable": {"thread_id": "owner:2:run:1"}})
    foreign = saver_for(bound(task_workspace, accepted, owner=2, version=2))
    with pytest.raises(NotFound):
        foreign.get_tuple(foreign._config())


def test_unleased_run_cannot_access_graph(task_workspace):
    _, accepted = new_task(task_workspace)
    factory, _, _ = task_workspace
    service = AgentGraphService(
        factory,
        owner_id=1,
        task_id=accepted.task.id,
        run_id=accepted.run.id,
        version=ENGINE_VERSION,
        fence=(1, None),
    )
    with pytest.raises(LeaseLost):
        saver_for(service).get_tuple(saver_for(service)._config())


def test_async_api_and_delete_all_namespaces(task_workspace):
    _, accepted = new_task(task_workspace)
    saver = saver_for(bound(task_workspace, accepted))

    async def exercise():
        for ns in ("", "child"):
            config = await saver.aput(saver._config(namespace=ns), empty_checkpoint(), {}, {})
            await saver.aput_writes(config, [("value", "ok")], "task")
            assert (await saver.aget_tuple(config)).pending_writes
        assert len([row async for row in saver.alist(None)]) == 2
        await saver.adelete_thread(saver.thread_id)
        assert [row async for row in saver.alist(None)] == []

    asyncio.run(exercise())


def test_conversation_purge_clears_every_graph_namespace(task_workspace):
    from sqlalchemy import func, select

    from short_drama.agent.session.purge import _scrub_locked
    from short_drama.domain.assistant.conversations.models import AgentConversation, AgentMessage
    from short_drama.domain.assistant.execution.graph import AgentGraphCheckpoint, AgentGraphWrite
    from short_drama.domain.assistant.execution.models import AgentToolCall
    from short_drama.domain.assistant.execution.scope import AgentExecutionScope
    from short_drama.domain.assistant.tasks.task import AgentTask
    from short_drama.service.assistant.execution.scope import AgentExecutionScopeService
    from tests.integration.standard.assistant.test_execution_scopes import calls_for
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_service

    conversation, accepted = new_task(task_workspace)
    graph = bound(task_workspace, accepted)
    saver = saver_for(graph)
    scopes = AgentExecutionScopeService(graph)
    root = scopes.root()
    calls = calls_for(graph.factory, accepted, root.id)
    child = scopes.child(root.id, calls[0])
    for namespace in ("", "child"):
        config = saver.put(saver._config(namespace=namespace), empty_checkpoint(), {}, {})
        saver.put_writes(config, [("value", "private")], "task")
    with graph.factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.status, run.finished_at = "succeeded", utcnow()
        run.lease_token = run.lease_until = None
        task = session.get(AgentTask, accepted.task.id)
        task.status, task.phase = "succeeded", "terminal"
        conversation_version = session.get(AgentConversation, conversation.id).row_version
    _, settings, _ = task_workspace
    with graph.factory() as session:
        task_service(session, settings).delete_global_conversation(
            conversation.id, {"row_version": conversation_version}, "delete-graphs"
        )
    with graph.factory.begin() as session:
        conv = session.get(AgentConversation, conversation.id)
        assert conv.status == "deleted"
        _scrub_locked(session, conv)
    with graph.factory() as session:
        for model in (AgentGraphCheckpoint, AgentGraphWrite):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        preserved = session.get(AgentExecutionScope, child.id)
        assert preserved.parent_tool_call_id == calls[0] and preserved.parent_scope_id == root.id
        assert session.get(AgentToolCall, calls[0]).arguments == {}
        assert session.get(AgentMessage, accepted.message.id).content == "[已删除]"


def test_framework_interrupt_resumes_after_rebuilding_saver_and_agent(task_workspace):
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langgraph.types import Command, interrupt

    from tests.support.assistant_model import ScriptedModel

    _, accepted = new_task(task_workspace)
    service = bound(task_workspace, accepted)
    executed = []

    @tool
    def deliver(name: str) -> str:
        """Deliver one branch after required input is available."""
        if name != "done":
            interrupt({"name": name})
        executed.append(name)
        return name

    async def exercise():
        saver = saver_for(service)
        config = saver._config()
        model = ScriptedModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "deliver", "args": {"name": name}, "id": name}
                        for name in ("done", "waiting-a", "waiting-b")
                    ],
                )
            ]
        )
        graph = create_agent(model, tools=[deliver], checkpointer=saver)
        await graph.ainvoke({"messages": [HumanMessage(content="分支交付")]}, config)
        snapshot = await graph.aget_state(config)
        assert len(snapshot.interrupts) == 2
        for waiting in snapshot.interrupts:
            resumed = create_agent(
                ScriptedModel(replies=[AIMessage(content="交付完成")]),
                tools=[deliver],
                checkpointer=saver_for(service),
            )
            result = await resumed.ainvoke(Command(resume={waiting.id: "继续"}), config)
        assert result["messages"][-1].content == "交付完成"
        assert sorted(executed) == ["done", "waiting-a", "waiting-b"]

    asyncio.run(exercise())


def test_pending_writes_follow_framework_task_path_order(task_workspace):
    _, accepted = new_task(task_workspace)
    saver = saver_for(bound(task_workspace, accepted))
    config = saver.put(saver._config(), empty_checkpoint(), {}, {})
    saver.put_writes(config, [("value", "first")], "z-task", "a-path")
    saver.put_writes(config, [("value", "second")], "a-task", "z-path")
    assert saver.get_tuple(config).pending_writes == [
        ("z-task", "value", "first"),
        ("a-task", "value", "second"),
    ]


def test_pending_writes_load_current_after_waiting_for_run_lock(task_workspace):
    _, accepted = new_task(task_workspace)
    service = bound(task_workspace, accepted)
    saver = saver_for(service)
    config = saver.put(saver._config(), empty_checkpoint(), {}, {})
    snapshot_created = Event()
    reader = local()
    reader_connection_ids = []
    reader_isolation_levels = []

    def observe_first_read(connection, _cursor, statement, _parameters, _context, _executemany):
        if getattr(reader, "loading", False) and not snapshot_created.is_set():
            assert "FOR UPDATE" not in statement
            reader_connection_ids.append(connection.connection.driver_connection.thread_id())
            reader_isolation_levels.append(connection.get_isolation_level())
            snapshot_created.set()

    def load():
        reader.loading = True
        return saver_for(service).get_tuple(config)

    pending = [
        ("completed", "answer", "committed"),
        ("waiting", "__interrupt__", [Interrupt(value={"question": "继续"}, id="wait")]),
    ]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service.factory.begin() as writer:
            assert writer.connection().get_isolation_level() == "REPEATABLE READ"
            assert lock_run(writer, accepted.run.id) is not None
            engine = writer.get_bind()
            event.listen(engine, "after_cursor_execute", observe_first_read)
            try:
                loading = pool.submit(load)
                assert snapshot_created.wait(10)
                assert reader_isolation_levels == ["REPEATABLE READ"]
                waiting = text(
                    "SELECT 1 FROM performance_schema.data_lock_waits AS waits "
                    "JOIN performance_schema.threads AS requesting "
                    "ON requesting.THREAD_ID = waits.REQUESTING_THREAD_ID "
                    "WHERE requesting.PROCESSLIST_ID = :connection_id"
                )
                deadline = monotonic() + 10
                while not writer.scalar(waiting, {"connection_id": reader_connection_ids[0]}):
                    assert monotonic() < deadline, "reader did not wait for the writer lock"
                # 先确认真实锁等待，再提交快照之后的节点回执和 interrupt。
                for task_key, channel, value in pending:
                    codec, payload = saver.serde.dumps_typed(value)
                    writer.add(
                        AgentGraphWrite(
                            id=next_id(),
                            owner_user_id=service.owner_id,
                            task_id=service.task_id,
                            run_id=service.run_id,
                            namespace="",
                            namespace_hash=sha256(b"").digest(),
                            checkpoint_id=config["configurable"]["checkpoint_id"],
                            task_key=task_key,
                            task_path="",
                            write_index=WRITES_IDX_MAP.get(channel, 0),
                            channel=channel,
                            codec=codec,
                            payload=payload,
                        )
                    )
                writer.flush()
            finally:
                event.remove(engine, "after_cursor_execute", observe_first_read)
        restored = loading.result(timeout=10)
    assert restored.pending_writes == pending
