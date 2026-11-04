"""Small graph projections backed by the original durable tool receipt."""

import hashlib
import json
from dataclasses import replace

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from short_drama.agent.context.budget import estimate_input_tokens, input_limit
from short_drama.agent.context.capabilities import context_profile
from short_drama.agent.context.subjects import eligible_snapshot, subject_memory_keys
from short_drama.service.assistant.context.subject import selection_metadata

SKILL_BODY_TOOLS = frozenset({"runtime__load_skill", "runtime__read_skill_resource"})
INLINE_TOOLS = SKILL_BODY_TOOLS | {
    "runtime__read_operation_receipt",
    "runtime__ask_user",
    "workbench__wait_generation",
}


def eligible_scope_keys(session, owner_id, snapshot, restricted):
    subjects = eligible_snapshot(snapshot, restricted)
    available = {
        item["subject_key"]
        for item in selection_metadata(session, owner_id, subjects)
        if item["availability"] != "unavailable"
    }
    return subject_memory_keys(
        {
            "subjects": [
                item
                for item in (subjects or {}).get("subjects", [])
                if item["subject_key"] in available
            ]
        }
    )


def source_context_eligible(context, memory_epoch, restricted, scope_keys, state=None):
    return bool(
        context is not None
        and context["memory_epoch"] == memory_epoch
        and set(context["subject_keys"]).issubset(scope_keys)
        and not {str(value) for value in restricted}.intersection(context["source_message_ids"])
        and (
            state is None
            or set(context["skill_activation_ids"]).issubset(
                item["id"] for item in state.get("skill_activations", []) if item.get("active")
            )
        )
    )


def result_text(content):
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def result_page(content, character_offset, character_limit):
    text = result_text(content)
    end = min(len(text), character_offset + character_limit)
    return {
        "content": text[character_offset:end],
        "total_characters": len(text),
        "checksum_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "next_offset": end if end < len(text) else None,
    }


def project_tool_result(output, *, operation_id, tool_name, snapshot, wait_state):
    if tool_name in INLINE_TOOLS or wait_state:
        return output
    profile = context_profile(snapshot)
    threshold = min(
        8192,
        input_limit(profile["max_output_tokens"], context_window=profile["context_window_tokens"])
        // 4,
    )
    if isinstance(output, Command):
        return replace(
            output,
            update={
                **output.update,
                "messages": [
                    _project_message(item, operation_id, threshold)
                    if isinstance(item, ToolMessage)
                    else item
                    for item in output.update["messages"]
                ],
            },
        )
    return _project_message(output, operation_id, threshold)


def _project_message(message, operation_id, threshold):
    text = result_text(message.content)
    if message.status == "error" or estimate_input_tokens({"content": text}) <= threshold:
        return message
    try:
        result = json.loads(text)
    except ValueError:
        result = None
    if isinstance(result, dict) and (result.get("hydration_intents") or result.get("error")):
        return message
    page = result_page(text, 0, 1000)
    return message.model_copy(
        update={
            "content": json.dumps(
                {
                    "tool_result_offloaded": True,
                    "receipt_operation_id": str(operation_id),
                    "preview": page["content"],
                    "total_characters": page["total_characters"],
                    "checksum_sha256": page["checksum_sha256"],
                    "read_with": {
                        "tool": "runtime__read_operation_receipt",
                        "arguments": {
                            "operation_id": str(operation_id),
                            "include_result": True,
                            "character_offset": 0,
                            "character_limit": 4000,
                        },
                    },
                },
                ensure_ascii=False,
            )
        }
    )
