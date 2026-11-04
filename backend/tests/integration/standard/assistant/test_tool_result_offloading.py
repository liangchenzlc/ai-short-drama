"""Original tool receipts remain durable while graph input uses small projections."""

import hashlib
import json

import pytest
from langchain_core.messages import ToolMessage, message_to_dict

from short_drama.agent.context.operation_receipts import read_receipt_locked
from short_drama.core.exceptions import NotFound
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.schemas.assistant.operations.tool_inputs import ReadReceipt
from short_drama.service.assistant.execution.tools import AgentToolExecutionService
from tests.integration.standard.assistant.test_agent_context_compaction import runtime
from tests.integration.standard.assistant.test_business_invocations import scope_operation
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import new_task

pytestmark = pytest.mark.integration


def test_original_result_survives_offloading_replay_and_authorized_paged_read(harness_workspace):
    conversation, accepted = new_task(harness_workspace)
    _saver, context = runtime(harness_workspace, accepted)
    operation_id = scope_operation(context.factory, accepted, context.scope_id, "execute")
    with context.factory.begin() as session:
        operation = session.get(AgentOperation, operation_id)
        call = session.get(AgentToolCall, operation.tool_call_id)
        call.arguments = {"parsed": {}}
        call.result = {
            "source_context": {
                "memory_epoch": 0,
                "subject_keys": [],
                "source_message_ids": [],
                "skill_activation_ids": [],
            }
        }
        provider_call_id = call.provider_call_id
        from short_drama.agent.runtime.events import operation_block_locked

        operation_block_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentRun, accepted.run.id),
            operation,
            spec=context.tool_lookup("execute"),
            state={"task": session.get(AgentTask, accepted.task.id), "state": {}},
        )
    original = json.dumps({"content": "完整工具结果\n" * 10000}, ensure_ascii=False)
    service = AgentToolExecutionService(context)
    projected = service.finish_tool(
        operation_id,
        ToolMessage(content=original, name="execute", tool_call_id=provider_call_id),
    )
    state = {"permitted_tool_names": ["execute"], "skill_activations": []}
    replay_id, replay = service.start_tool(
        {"id": provider_call_id, "name": "execute", "args": {}}, state
    )
    assert replay_id == operation_id
    assert message_to_dict(replay) == message_to_dict(projected)
    assert json.loads(projected.content)["tool_result_offloaded"]
    with context.factory.begin() as session:
        operation = session.get(AgentOperation, operation_id)
        call = session.get(AgentToolCall, operation.tool_call_id)
        assert call.result["message"]["data"]["content"] == original
        assert operation.result["content"] == "完整工具结果\n" * 10000
        task = session.get(AgentTask, accepted.task.id)
        conversation = session.get(AgentConversation, conversation.id)
        pages, offset = [], 0
        while offset is not None:
            result = read_receipt_locked(
                session,
                conversation,
                task,
                operation,
                ReadReceipt(
                    operation_id=operation_id,
                    include_result=True,
                    character_offset=offset,
                    character_limit=6000,
                ),
                state=state,
            )
            pages.append(result["result_page"])
            offset = result["result_page"]["next_offset"]
        assert "".join(page["content"] for page in pages) == original
        assert {page["checksum_sha256"] for page in pages} == {
            hashlib.sha256(original.encode()).hexdigest()
        }
        values = ReadReceipt(operation_id=operation_id, include_result=True)
        run = session.get(AgentRun, accepted.run.id)
        run.checkpoint = {**run.checkpoint, "memory_epoch": 1}
        with pytest.raises(NotFound):
            read_receipt_locked(session, conversation, task, operation, values, state=state)
        run.checkpoint = {**run.checkpoint, "memory_epoch": 0}
        call.result = {
            **call.result,
            "source_context": {
                **call.result["source_context"],
                "skill_activation_ids": ["released"],
            },
        }
        with pytest.raises(NotFound):
            read_receipt_locked(session, conversation, task, operation, values, state=state)
        call.result = {
            **call.result,
            "source_context": {**call.result["source_context"], "skill_activation_ids": []},
        }
        operation.tool_name = "runtime__load_skill"
        with pytest.raises(NotFound):
            read_receipt_locked(session, conversation, task, operation, values, state=state)
