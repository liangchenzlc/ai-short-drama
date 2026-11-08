"""Real MySQL execution boundaries; suppliers and storage remain controlled doubles."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import timedelta
from threading import Event, local
from time import monotonic
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError

from short_drama.agent.core.access import LeaseLost
from short_drama.agent.task.state import apply_inputs_locked
from short_drama.agent.tools.langchain import create_langchain_tool
from short_drama.agent.tools.registry import get_spec
from short_drama.agent.tools.runtime import invoke_runtime_tool
from short_drama.ai import GenerationResult
from short_drama.core.exceptions import NotFound
from short_drama.core.time import utcnow
from short_drama.domain import AIGenerationRecord, Asset, AsyncTask
from short_drama.domain.assistant.context.context import AgentAttachment
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask, AgentTaskInput
from short_drama.service.assistant.execution.scope import AgentExecutionScopeService
from short_drama.service.assistant.settings.preferences import AssistantSettingsService
from tests.integration.standard.assets.test_asset_image_generation import (
    ImageProvider,
    create_asset,
    drain,
)
from tests.integration.standard.assets.test_asset_image_generation import (
    flow as flow,
)
from tests.integration.standard.assistant.test_business_invocations import (
    business_identity,
    invocation_api,
    issued_mcp_token,
    scope_operation,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    new_task,
    task_service,
)
from tests.integration.standard.assistant.test_graph_checkpointer import bound

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


@dataclass
class RuntimeContext:
    owner_id: int
    run_id: int
    scope_id: int
    fence: tuple[int, str]
    factory: Any
    settings: Any
    tool_lookup: Any


@pytest.mark.parametrize(
    "fence,token,seconds,permitted",
    [
        pytest.param(None, "a" * 32, 300, False, id="missing-fence"),
        pytest.param((1, None), None, 300, False, id="missing-token"),
        pytest.param((1, ""), "", 300, False, id="empty-token"),
        pytest.param((0, "a" * 32), "a" * 32, 300, False, id="old-version"),
        pytest.param((1, "b" * 32), "a" * 32, 300, False, id="old-token"),
        pytest.param((1, "a" * 32), "a" * 32, -1, False, id="expired"),
        pytest.param((1, "a" * 32), "a" * 32, 300, True, id="valid"),
    ],
)
def test_public_runtime_entry_requires_current_nonempty_lease(
    task_workspace, fence, token, seconds, permitted
):
    factory, settings, _model = task_workspace
    conversation, accepted = new_task(task_workspace)
    graph = bound(task_workspace, accepted)
    root = AgentExecutionScopeService(graph).root()
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        apply_inputs_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
            run,
        )
    operation_id = scope_operation(factory, accepted, root.id, "runtime__describe_creation_target")
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.lease_token = token
        run.lease_until = utcnow() + timedelta(seconds=seconds) if token is not None else None
        operation = session.get(AgentOperation, operation_id)
        call_id = session.get(AgentToolCall, operation.tool_call_id).provider_call_id
    context = SimpleNamespace(
        owner_id=1,
        run_id=accepted.run.id,
        scope_id=root.id,
        fence=fence,
        factory=factory,
        settings=settings,
        tool_lookup=get_spec,
    )

    def save_marker(session, conversation, task, operation, values, settings, **kwargs):
        operation.result = {"executed": True}
        return operation.result

    if permitted:
        assert invoke_runtime_tool(save_marker, None, context, call_id) == {"executed": True}
    else:
        with pytest.raises(LeaseLost):
            invoke_runtime_tool(save_marker, None, context, call_id)
    with factory() as session:
        assert session.get(AgentOperation, operation_id).result == (
            {"executed": True} if permitted else None
        )


def admit_business_image(flow, request_id="business-image", *, source="mcp"):
    from short_drama.schemas.assistant.operations.media import SubmitGenerationBatch
    from short_drama.service.assistant.operations.media import submit_generation_batch_locked

    api = invocation_api()
    asset = create_asset(flow)
    identity = business_identity(
        api, flow.factory, flow.session.info["actor"], flow.project.id, request_id
    )
    identity = replace(
        identity,
        source=source,
        token_id=identity.token_id if source == "mcp" else None,
        scope=replace(
            identity.scope,
            tool_names=frozenset(
                {"workbench__submit_generation_batch", "workbench__get_generation_status"}
            ),
        ),
    )
    values = SubmitGenerationBatch(
        project_id=flow.project.id,
        requests=[
            {
                "target_kind": "asset",
                "target_id": str(asset.id),
                "row_version": str(asset.row_version),
                "model_config_id": str(flow.config.id),
                "model_version": str(flow.config.row_version),
                "prompt": "受控图片提示词",
                "count": 1,
            }
        ],
    )
    with flow.factory() as session:
        result = api.BusinessInvocationService(session, flow.settings).execute(
            identity,
            "workbench__submit_generation_batch",
            values,
            submit_generation_batch_locked,
            mutating=True,
        )
    return int(result["items"][0]["generation_id"]), identity, result


def disable_media(flow):
    with flow.factory() as session:
        session.info["actor"] = flow.session.info["actor"]
        return AssistantSettingsService(session, flow.settings).update_settings(
            {"settings_version": "0", "media_generation_enabled": False}
        )


@pytest.mark.parametrize("stage", ["claim", "paid-submit"])
def test_mcp_media_uses_token_permission_when_internal_assistant_is_disabled(flow, stage):
    task_id, _identity, _result = admit_business_image(flow)
    with flow.factory() as session:
        version = session.get(AsyncTask, task_id).message_version
    if stage == "claim":
        snapshot_created, policy_changed = Event(), Event()
        worker = local()

        def after_first_read(connection, cursor, statement, parameters, context, executemany):
            if getattr(worker, "executing", False) and not snapshot_created.is_set():
                assert connection.get_isolation_level() == "REPEATABLE READ"
                assert "FOR UPDATE" not in statement
                snapshot_created.set()
                assert policy_changed.wait(10)

        def execute():
            worker.executing = True
            flow.executor.execute(task_id, version)

        engine = flow.session.get_bind()
        event.listen(engine, "after_cursor_execute", after_first_read)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                execution = pool.submit(execute)
                try:
                    assert snapshot_created.wait(10)
                    disable_media(flow)
                finally:
                    policy_changed.set()
                execution.result(timeout=10)
        finally:
            event.remove(engine, "after_cursor_execute", after_first_read)
    else:
        claim = flow.executor.store.claim_execution(task_id, version)
        assert claim is not None
        task, record, token = claim
        disable_media(flow)
        flow.executor._generate(task, record, version, token)
    assert drain(flow, task_id) == "succeeded"
    with flow.factory() as session:
        task = session.get(AsyncTask, task_id)
        assert task.status == "succeeded" and not task.error
    assert len(flow.provider.calls) == 1


@pytest.mark.parametrize(
    "source,closure,adopted",
    [
        ("assistant", "media", False),
        ("assistant", "workbench", False),
        ("mcp", "workbench", False),
        ("mcp", "revoked", False),
    ],
)
def test_business_media_already_sent_archives_candidates_without_changing_primary(
    flow, source, closure, adopted
):
    from short_drama.agent.tools.registry import TOOL_SPECS
    from short_drama.domain import ImageGroup, ImageGroupItem, MediaAsset
    from short_drama.schemas.assistant.operations.media import GetGenerationStatus
    from short_drama.service.assistant.operations.media import get_generation_status_locked
    from short_drama.service.identity.accounts.mcp_token import McpTokenService

    class DeferredImages(ImageProvider):
        def __init__(self):
            super().__init__()
            self.polls = 0

        def submit(self, snapshot, request, *args, **kwargs):
            self.completed = super().submit(snapshot, request, *args, **kwargs)
            return GenerationResult(
                status="submitted", adapter="ark_images.v1", provider_task_id="controlled-image"
            )

        def poll(self, snapshot, provider_task_id, credential, adapter, **kwargs):
            assert provider_task_id == "controlled-image"
            self.polls += 1
            return deepcopy(self.completed)

    provider = DeferredImages()
    flow.executor.gateway = provider
    task_id, identity, admitted = admit_business_image(flow, source=source)
    with flow.factory() as session:
        task = session.get(AsyncTask, task_id)
        flow.executor.execute(task_id, task.message_version)
    assert len(provider.calls) == 1
    if closure == "media":
        disable_media(flow)
    elif closure == "workbench":
        with flow.factory() as session:
            session.info["actor"] = flow.session.info["actor"]
            AssistantSettingsService(session, flow.settings).update_settings(
                {"settings_version": "0", "workbench_enabled": False}
            )
    else:
        with flow.factory() as session:
            session.info["actor"] = flow.session.info["actor"]
            McpTokenService(session, tool_specs=TOOL_SPECS).delete(identity.token_id)
    assert drain(flow, task_id) == "succeeded"
    if closure == "revoked":
        identity = replace(
            identity, token_id=issued_mcp_token(flow.factory, flow.session.info["actor"])
        )
    with flow.factory() as session:
        ready = (
            invocation_api()
            .BusinessInvocationService(session)
            .execute(
                identity,
                "workbench__get_generation_status",
                GetGenerationStatus(project_id=flow.project.id, generation_ids=[task_id]),
                get_generation_status_locked,
                mutating=False,
            )
        )
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        assert (
            record.config_snapshot["business_invocation_id"] == admitted["business_invocation_id"]
        )
        output = session.scalar(select(MediaAsset).where(MediaAsset.record_id == record.id))
        group = session.scalar(select(ImageGroup).where(ImageGroup.task_id == task_id))
        item = session.scalar(select(ImageGroupItem).where(ImageGroupItem.group_id == group.id))
        asset = session.get(Asset, int(record.config_snapshot["business_media"]["id"]))
        assert output.media_id == item.media_id
        assert asset.media_id == (output.media_id if adopted else None)
        assert asset.row_version == (2 if adopted else 1)
        assert record.response_data["business_adoption"]["adopted"] is adopted
        if not adopted:
            assert group.primary_index is None
    assert ready["settled"] and ready["items"][0]["result_refs"]
    assert len(provider.calls) == 1 and provider.polls == 1


@pytest.mark.parametrize("origin", ["external", "global-tool-node"])
def test_business_write_and_native_claim_share_project_first_lock_order(flow, origin):
    from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
    from short_drama.service.assistant.operations.standard import apply_standard_patch_locked

    task_id, _identity, _admitted = admit_business_image(flow)
    with flow.factory() as session:
        version = session.get(AsyncTask, task_id).message_version
    api = invocation_api()
    identity = business_identity(
        api, flow.factory, flow.session.info["actor"], flow.project.id, "concurrent-write"
    )
    values = ApplyStandardPatch(
        project_id=flow.project.id,
        patch={
            "actions": [
                {
                    "action": "create_asset",
                    "key": "concurrent",
                    "values": {"name": "并发业务保存", "kind": "prop"},
                }
            ]
        },
    )
    if origin == "global-tool-node":
        from tests.integration.standard.assistant.test_global_assistant_tasks import _workspace

        workspace = _workspace(flow.session)
        factory, settings, model = workspace
        with factory() as session:
            service = task_service(session, settings)
            conversation = service.create_global_conversation({}, uuid4().hex)
            assert conversation.project_id is None
            accepted = service.send_task_message(
                conversation.id,
                {
                    "content": "创建一个并发保存的道具",
                    "model_config_id": model,
                    "execution_target": {"mode": "standard", "project_id": str(flow.project.id)},
                },
                uuid4().hex,
            )
        root = AgentExecutionScopeService(bound(workspace, accepted)).root()
        with factory.begin() as session:
            assert apply_inputs_locked(
                session,
                session.get(AgentConversation, conversation.id),
                session.get(AgentTask, accepted.task.id),
                session.get(AgentRun, accepted.run.id),
            )
        arguments = values.model_dump(mode="json")
        operation_id = scope_operation(
            factory, accepted, root.id, "workbench__apply_standard_patch", arguments
        )
        with factory() as session:
            operation = session.get(AgentOperation, operation_id)
            call_id = session.get(AgentToolCall, operation.tool_call_id).provider_call_id

        runtime_context = RuntimeContext(
            1, accepted.run.id, root.id, (1, "a" * 32), factory, settings, get_spec
        )
        builder = StateGraph(MessagesState, context_schema=RuntimeContext)
        builder.add_node(
            "tools",
            ToolNode(
                [create_langchain_tool(get_spec("workbench__apply_standard_patch"))],
                handle_tool_errors=False,
            ),
        )
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()
        graph_input = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "workbench__apply_standard_patch",
                            "args": arguments,
                            "id": call_id,
                        }
                    ],
                )
            ]
        }
    project_locked, writer_started, release_worker = Event(), Event(), Event()
    role = ContextVar("business_lock_role", default=None)
    writer_connections = []
    engine = flow.session.get_bind()

    def observe_locks(connection, cursor, statement, parameters, context, executemany):
        if role.get() == "worker" and not project_locked.is_set():
            if "FROM projects" in statement and "FOR UPDATE" in statement:
                project_locked.set()
                assert release_worker.wait(15)

    def observe_writer_start(connection, cursor, statement, parameters, context, executemany):
        if role.get() == "writer" and not writer_started.is_set():
            writer_connections.append(connection.connection.driver_connection.thread_id())
            writer_started.set()

    def worker_claim():
        role.set("worker")
        return flow.executor.store.claim_execution(task_id, version)

    def business_write():
        role.set("writer")
        if origin == "global-tool-node":
            result = asyncio.run(graph.ainvoke(graph_input, context=runtime_context))
            return json.loads(result["messages"][-1].content)
        with flow.factory() as session:
            return api.BusinessInvocationService(session).execute(
                identity,
                "workbench__apply_standard_patch",
                values,
                apply_standard_patch_locked,
                mutating=True,
            )

    waiting = text(
        "SELECT 1 FROM performance_schema.data_lock_waits AS waits "
        "JOIN performance_schema.threads AS requesting "
        "ON requesting.THREAD_ID = waits.REQUESTING_THREAD_ID "
        "JOIN performance_schema.data_locks AS requested "
        "ON requested.ENGINE_LOCK_ID = waits.REQUESTING_ENGINE_LOCK_ID "
        "WHERE requesting.PROCESSLIST_ID = :connection_id AND requested.OBJECT_NAME = 'projects'"
    )
    event.listen(engine, "after_cursor_execute", observe_locks)
    event.listen(engine, "before_cursor_execute", observe_writer_start)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            claim = pool.submit(worker_claim)
            try:
                assert project_locked.wait(10)
                saved = pool.submit(business_write)
                # The writer may block on its very first SELECT after the fix.
                # before_cursor_execute identifies that connection without replacing the lock query.
                deadline = monotonic() + 10
                with engine.connect() as probe:
                    while not writer_started.is_set():
                        assert monotonic() < deadline, "business writer did not start"
                        writer_started.wait(0.01)
                    while not probe.scalar(waiting, {"connection_id": writer_connections[0]}):
                        assert monotonic() < deadline, (
                            "writer did not wait on the real Project lock"
                        )
                release_worker.set()
            finally:
                release_worker.set()
            errors, results = [], []
            for future in (claim, saved):
                try:
                    results.append(future.result(timeout=10))
                except OperationalError as error:
                    errors.append(error.orig.args)
            assert errors == [], f"native claim/business write deadlocked: {errors}"
    finally:
        event.remove(engine, "after_cursor_execute", observe_locks)
        event.remove(engine, "before_cursor_execute", observe_writer_start)
    assert results[0] is not None and results[1]["business_invocation_id"]
    if origin == "global-tool-node":
        assert business_write() == results[1]
    with flow.factory() as session:
        if origin == "external":
            replay = api.BusinessInvocationService(session).execute(
                replace(
                    identity,
                    token_id=issued_mcp_token(flow.factory, flow.session.info["actor"]),
                ),
                "workbench__apply_standard_patch",
                values,
                apply_standard_patch_locked,
                mutating=True,
            )
            assert replay == results[1]
        assert (
            session.scalar(
                select(func.count()).select_from(Asset).where(Asset.name == "并发业务保存")
            )
            == 1
        )


@pytest.mark.parametrize(
    "binding",
    [
        "execution_target",
        "script_reference",
        "subject_selection",
        "asset_attachment",
        "media_attachment",
    ],
)
def test_global_message_and_tool_have_consistent_lock_order(flow, binding):
    from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
    from short_drama.service.assistant.context.attachment import AgentAttachmentService
    from short_drama.service.creation.writing.script.documents import ProjectWritingService
    from short_drama.service.media.files.storage import StorageService
    from short_drama.service.media.image_groups.groups import ImageGroupService
    from tests.integration.standard.assistant.test_global_assistant_tasks import _workspace

    workspace = _workspace(flow.session)
    factory, settings, model = workspace
    actor = flow.session.info["actor"]
    target = {"mode": "standard", "project_id": str(flow.project.id), "canvas_id": None}
    references = {"execution_target": target}
    attachment_source = None
    if binding == "script_reference":
        saved = ProjectWritingService(flow.session).save_script(
            flow.project.id,
            {
                "content_version": "1",
                "script_id": None,
                "content": "雨夜车站里，主角发现了一封信。",
            },
        )
        references[binding] = {
            "project_id": str(flow.project.id),
            "script_id": saved["script"]["id"],
            "content_version": saved["content_version"],
            "start": 2,
            "end": 8,
        }
    elif binding == "subject_selection":
        asset = create_asset(flow)
        references[binding] = {
            "subjects": [
                {
                    "project_id": str(flow.project.id),
                    "source_kind": "project",
                    "source_id": str(flow.project.id),
                    "kind": "asset",
                    "id": str(asset.id),
                    "revision": str(asset.row_version),
                }
            ]
        }
    elif binding == "asset_attachment":
        attachment_source = {"source_type": "asset", "source_id": str(create_asset(flow).id)}
    elif binding == "media_attachment":
        task_id, _identity, admitted = admit_business_image(flow)
        assert drain(flow, task_id) == "succeeded"
        group = ImageGroupService(flow.session).current("asset", admitted["items"][0]["target_id"])
        attachment_source = {"source_type": "media", "source_id": group["items"][0]["media_id"]}
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        assert conversation.project_id is None
        accepted = service.send_task_message(
            conversation.id,
            {"content": "保存一个道具", "model_config_id": model, "execution_target": target},
            uuid4().hex,
        )
        if binding == "media_attachment":
            with pytest.raises(NotFound, match="Reference is outside this resource scope"):
                AgentAttachmentService(
                    session, settings, StorageService(flow.storage, flow.settings)
                ).reference(conversation.id, attachment_source, uuid4().hex)
        if attachment_source is None:
            supplement = {
                "task_id": str(accepted.task.id),
                "content": "补充颜色要求",
                binding: references[binding],
            }
            if binding == "subject_selection":
                supplement[binding]["expected_input_revision"] = str(accepted.task.input_revision)
            if binding in {"script_reference", "subject_selection"}:
                # 先真实接受同类引用，确保并发失败不是无效输入造成的。
                accepted = service.send_task_message(conversation.id, supplement, uuid4().hex)
                assert int(accepted.task.input_revision) == 2
                if binding == "subject_selection":
                    supplement[binding]["expected_input_revision"] = str(
                        accepted.task.input_revision
                    )
    root = AgentExecutionScopeService(bound(workspace, accepted)).root()
    with factory.begin() as session:
        assert apply_inputs_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
            session.get(AgentRun, accepted.run.id),
        )
    arguments = ApplyStandardPatch(
        project_id=flow.project.id,
        patch={
            "actions": [
                {
                    "action": "create_asset",
                    "key": "prop",
                    "values": {"kind": "prop", "name": "补充消息并发道具"},
                }
            ]
        },
    ).model_dump(mode="json")
    operation_id = scope_operation(
        factory,
        accepted,
        root.id,
        "workbench__apply_standard_patch",
        arguments,
        input_revision=int(accepted.task.input_revision),
    )
    with factory() as session:
        operation = session.get(AgentOperation, operation_id)
        call_id = session.get(AgentToolCall, operation.tool_call_id).provider_call_id
    runtime_context = RuntimeContext(
        1, accepted.run.id, root.id, (1, "a" * 32), factory, settings, get_spec
    )
    builder = StateGraph(MessagesState, context_schema=RuntimeContext)
    builder.add_node(
        "tools",
        ToolNode(
            [create_langchain_tool(get_spec("workbench__apply_standard_patch"))],
            handle_tool_errors=False,
        ),
    )
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()
    graph_input = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workbench__apply_standard_patch",
                        "args": arguments,
                        "id": call_id,
                    }
                ],
            )
        ]
    }
    role = ContextVar("message_tool_lock_role", default=None)
    message_locked, release_message = Event(), Event()
    tool_project_locked, tool_waiting = Event(), Event()
    tool_connections = []
    engine = flow.session.get_bind()

    def after_query(connection, cursor, statement, parameters, context, executemany):
        if (
            role.get() == "message"
            and not message_locked.is_set()
            and "FROM agent_conversations" in statement
            and "FOR UPDATE" in statement
        ):
            message_locked.set()
            assert release_message.wait(15), "message gate timed out"
        if role.get() == "tool" and "FROM projects" in statement and "FOR UPDATE" in statement:
            tool_project_locked.set()

    def before_query(connection, cursor, statement, parameters, context, executemany):
        if (
            role.get() == "tool"
            and not tool_waiting.is_set()
            and "FROM agent_conversations" in statement
            and "FOR UPDATE" in statement
        ):
            tool_connections.append(connection.connection.driver_connection.thread_id())
            tool_waiting.set()

    def send_message():
        role.set("message")
        with factory() as session:
            if attachment_source is not None:
                session.info["actor"] = actor
                attachments = AgentAttachmentService(
                    session, settings, StorageService(flow.storage, flow.settings)
                )
                if binding == "media_attachment":
                    with pytest.raises(NotFound, match="Reference is outside this resource scope"):
                        attachments.reference(conversation.id, attachment_source, uuid4().hex)
                    return None
                return attachments.reference(conversation.id, attachment_source, uuid4().hex)
            return task_service(session, settings).send_task_message(
                conversation.id, supplement, uuid4().hex
            )

    def invoke_tool():
        role.set("tool")
        return asyncio.run(graph.ainvoke(graph_input, context=runtime_context))

    waiting = text(
        "SELECT 1 FROM performance_schema.data_lock_waits AS waits "
        "JOIN performance_schema.threads AS requesting "
        "ON requesting.THREAD_ID = waits.REQUESTING_THREAD_ID "
        "JOIN performance_schema.data_locks AS requested "
        "ON requested.ENGINE_LOCK_ID = waits.REQUESTING_ENGINE_LOCK_ID "
        "WHERE requesting.PROCESSLIST_ID = :connection_id "
        "AND requested.OBJECT_NAME = 'agent_conversations'"
    )
    event.listen(engine, "after_cursor_execute", after_query)
    event.listen(engine, "before_cursor_execute", before_query)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            message = pool.submit(send_message)
            try:
                assert message_locked.wait(10), "message did not acquire Conversation"
                tool = pool.submit(invoke_tool)
                assert tool_project_locked.wait(10), "tool did not acquire Project"
                assert tool_waiting.wait(10), "tool did not request Conversation"
                deadline = monotonic() + 10
                with engine.connect() as probe:
                    while not probe.scalar(waiting, {"connection_id": tool_connections[0]}):
                        assert monotonic() < deadline, (
                            "tool did not wait for real Conversation lock"
                        )
            finally:
                release_message.set()
            submitted = message.result(timeout=15)
            saved = tool.result(timeout=15)
    finally:
        release_message.set()
        event.remove(engine, "after_cursor_execute", after_query)
        event.remove(engine, "before_cursor_execute", before_query)
    content = json.loads(saved["messages"][-1].content)
    assert content["business_invocation_id"] and content["result_refs"]
    with factory() as session:
        receipt = session.get(BusinessToolInvocation, int(content["business_invocation_id"]))
        assert receipt.status == "succeeded" and receipt.result_refs == content["result_refs"]
        if binding == "media_attachment":
            assert submitted is None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentAttachment)
                    .where(AgentAttachment.conversation_id == int(conversation.id))
                )
                == 0
            )
        elif attachment_source is not None:
            attachment = session.get(AgentAttachment, int(submitted.id))
            assert attachment.conversation_id == int(conversation.id)
            assert attachment.owner_user_id == actor.user_id
            assert attachment.deleted_at is None and attachment.attached_message_id is None
            assert attachment.input_metadata["source_type"] == attachment_source["source_type"]
            assert attachment.input_metadata["source_id"] == attachment_source["source_id"]
            source = session.get(Asset, int(attachment_source["source_id"]))
            assert attachment.kind == "text" and attachment.media_id is None
            assert json.loads(attachment.text_content)["name"] == source.name
            assert attachment.input_metadata["source_row_version"] == str(source.row_version)
        else:
            pending = session.scalar(
                select(AgentTaskInput).where(AgentTaskInput.message_id == int(submitted.message.id))
            )
            if binding == "subject_selection":
                selected = pending.payload["context_snapshot"]["subjects"]
                requested = supplement[binding]["subjects"][0]
                assert len(selected) == 1 and selected[0]["id"] == requested["id"]
                assert selected[0]["revision"] == requested["revision"]
                assert selected[0]["image_catalog"]["target_id"] == requested["id"]
                assert selected[0]["image_catalog"]["target_version"] == requested["revision"]
            else:
                assert pending.payload[binding] == supplement[binding]
            assert pending.applied_plan_revision is None
        assert (
            session.scalar(
                select(func.count()).select_from(Asset).where(Asset.name == "补充消息并发道具")
            )
            == 1
        )


def test_persisted_image_read_survives_model_projection_and_new_session(flow):
    import hashlib

    from short_drama.agent.resources.resolver import ResourceResolver
    from short_drama.agent.runtime.executor import AgentRuntime
    from short_drama.service.media.image_groups.groups import ImageGroupService
    from tests.integration.standard.assistant.test_global_assistant_tasks import _workspace
    from tests.support.assistant_runtime import RuntimeTransport, calls, tool_result
    from tests.unit.standard.agent.test_harness_resources import ObjectStore

    task_id, _identity, admitted = admit_business_image(flow)
    assert drain(flow, task_id) == "succeeded"
    asset_id = admitted["items"][0]["target_id"]
    group = ImageGroupService(flow.session).current("asset", asset_id)
    item = group["items"][0]
    resource_storage = ObjectStore()
    resource_storage.objects.update(
        {
            f"minio://{bucket}/{key}": data
            for (bucket, key), (data, _mime) in flow.storage.objects.items()
        }
    )
    factory, settings, model = _workspace(flow.session)
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "保留已核对的图片并继续创作",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": str(flow.project.id)},
                "subject_selection": {
                    "expected_input_revision": "0",
                    "subjects": [
                        {
                            "project_id": str(flow.project.id),
                            "source_kind": "project",
                            "source_id": str(flow.project.id),
                            "kind": "asset",
                            "id": asset_id,
                            "revision": group["target_version"],
                        }
                    ],
                },
            },
            uuid4().hex,
        )

    def pixels(body):
        arguments = json.loads(tool_result(body, "catalog")["content"])["groups"][0]["items"][0][
            "read_content_args"
        ]
        return calls(
            ("pixels", "workbench__read_image_content", {**arguments, "purpose": "核对已保存像素"})
        )

    transport = RuntimeTransport(
        [
            calls(
                (
                    "catalog",
                    "workbench__read_image_group",
                    {
                        "project_id": str(flow.project.id),
                        "target_kind": "asset",
                        "target_id": asset_id,
                        "view": "current",
                    },
                )
            ),
            pixels,
            {"text": "已依据已保存图片完成讨论"},
        ]
    )
    AgentRuntime(
        factory,
        settings,
        transport=transport,
        resolver_factory=lambda: ResourceResolver(resource_storage),
    ).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        read = session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id,
                AgentOperation.tool_name == "workbench__read_image_content",
            )
        )
        assert read.status == "succeeded"
        read_id = read.id
        assert tool_result(transport.main_requests[2], "pixels")["receipt_operation_id"] == str(
            read.id
        )
        assert "data:image/" in json.dumps(transport.main_requests[2])
    with factory() as session:
        restored = session.get(AgentOperation, read_id)
        assert restored.result["media_id"] == item["media_id"]
        deliveries = session.get(AgentToolCall, restored.tool_call_id).result["resource_deliveries"]
        assert len(deliveries) == 1
        reference = deliveries[0]["resource"]
        assert reference["metadata"]["original_ref"]["metadata"]["media_id"] == item["media_id"]
        assert (
            hashlib.sha256(resource_storage.objects[reference["uri"]]).hexdigest()
            == reference["checksum"]
        )
