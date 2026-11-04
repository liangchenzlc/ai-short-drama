import hashlib
import json
from copy import deepcopy

import pytest
from langchain_core.messages import ToolMessage, message_to_dict
from langgraph.types import Command
from pydantic import ValidationError

from short_drama.agent.context import tool_results
from short_drama.schemas.assistant.operations.tool_inputs import ReadReceipt


def test_large_result_is_recoverable_without_mutating_saved_message_or_command():
    message = ToolMessage(
        content=json.dumps({"content": "原始正文。" * 10000}, ensure_ascii=False),
        name="execute",
        id="result-message",
        tool_call_id="call-1",
        additional_kwargs={"source_message_ids": ["12"], "subject_keys": ["subject:1"]},
    )
    saved = deepcopy(message_to_dict(message))
    output = Command(update={"messages": [message], "files": {"/draft.md": "saved"}}, goto="next")

    projected = tool_results.project_tool_result(
        output, operation_id=10, tool_name="execute", snapshot={}, wait_state=None
    )

    visible = projected.update["messages"][0]
    result = json.loads(visible.content)
    assert message_to_dict(message) == saved
    assert visible.id == message.id
    assert visible.tool_call_id == message.tool_call_id
    assert visible.additional_kwargs == message.additional_kwargs
    assert projected.update["files"] == output.update["files"]
    assert projected.goto == output.goto
    assert len(visible.content) < 3000
    assert result["receipt_operation_id"] == "10"
    assert result["checksum_sha256"] == hashlib.sha256(message.content.encode()).hexdigest()
    assert result["read_with"]["arguments"] == {
        "operation_id": "10",
        "include_result": True,
        "character_offset": 0,
        "character_limit": 4000,
    }


@pytest.mark.parametrize(
    ("tool_name", "content", "wait_state"),
    [
        ("runtime__load_skill", {"instructions": "方法正文" * 20000}, None),
        ("runtime__read_skill_resource", {"content": "资料正文" * 20000}, None),
        ("workbench__read_image_content", {"hydration_intents": [{"data": "x" * 40000}]}, None),
        ("runtime__ask_user", {"answer": "用户回答" * 20000}, {"kind": "user_input"}),
        ("workbench__wait_generation", {"content": "生成状态" * 20000}, {"kind": "generation"}),
        ("execute", {"error": {"message": "错误详情" * 20000}}, None),
    ],
)
def test_active_protocol_results_are_kept(tool_name, content, wait_state):
    message = ToolMessage(content=json.dumps(content), tool_call_id="call", name=tool_name)
    assert (
        tool_results.project_tool_result(
            message, operation_id=10, tool_name=tool_name, snapshot={}, wait_state=wait_state
        )
        is message
    )


def test_result_pages_reassemble_original_body_with_one_checksum():
    content = json.dumps({"content": "完整内容\n" * 3000}, ensure_ascii=False)
    pages, offset = [], 0
    while offset is not None:
        page = tool_results.result_page(content, offset, 4000)
        pages.append(page)
        offset = page["next_offset"]
    assert "".join(page["content"] for page in pages) == content
    assert {page["total_characters"] for page in pages} == {len(content)}
    assert {page["checksum_sha256"] for page in pages} == {
        hashlib.sha256(content.encode()).hexdigest()
    }


def test_offload_threshold_tracks_declared_remaining_input_capacity():
    message = ToolMessage(content="x" * 3000, tool_call_id="call", name="execute")
    snapshot = {
        "capability_cache": {
            "agent_inputs": {"context_window_tokens": 16384, "max_output_tokens": 8192}
        }
    }
    assert (
        tool_results.project_tool_result(
            message, operation_id=10, tool_name="execute", snapshot={}, wait_state=None
        )
        is message
    )
    projected = tool_results.project_tool_result(
        message, operation_id=10, tool_name="execute", snapshot=snapshot, wait_state=None
    )
    assert json.loads(projected.content)["tool_result_offloaded"]


def test_receipt_page_input_bounds():
    values = ReadReceipt(operation_id="10", include_result=True)
    assert values.character_offset == 0
    assert values.character_limit == 4000
    with pytest.raises(ValidationError):
        ReadReceipt(operation_id="10", character_limit=6001)


@pytest.mark.parametrize(
    "change",
    [
        {"memory_epoch": 1},
        {"subject_keys": ["other"]},
        {"source_message_ids": ["99"]},
        {"skill_activation_ids": ["released"]},
    ],
)
def test_result_source_eligibility_rejects_revoked_context(change):
    context = {
        "memory_epoch": 2,
        "subject_keys": ["subject"],
        "source_message_ids": ["12"],
        "skill_activation_ids": ["active"],
    }
    state = {"skill_activations": [{"id": "active", "active": True}]}
    assert tool_results.source_context_eligible(context, 2, {99}, {"subject"}, state)
    assert not tool_results.source_context_eligible(
        {**context, **change}, 2, {99}, {"subject"}, state
    )
