"""Fresh L1–L5 projection plus lightweight transcript/resource manifests."""

import json
import logging
import time
from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.context.attachments import (
    attachment_keys,
    available_attachment_ids,
    task_attachments,
)
from short_drama.agent.context.budget import SAFETY_TOKENS, TOOL_RESULT_TOKENS
from short_drama.agent.context.capabilities import context_profile
from short_drama.agent.context.subjects import snapshot_subjects, subject_memory_keys
from short_drama.agent.context.transcript import freeze_transcript
from short_drama.agent.context.working_set import projection as working_projection
from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.harness.supervision import prepare_review
from short_drama.agent.memory.context import assemble_memory_locked
from short_drama.agent.memory.sources import restricted_messages
from short_drama.agent.resources.catalog import attachments_locked
from short_drama.agent.resources.contracts import assert_reference_payload
from short_drama.agent.resources.resolver import attachment_ref, skill_resource_ref
from short_drama.agent.skill.contracts import ReadSkillResource, activation_summary, activations
from short_drama.agent.skill.progressive import catalog_locked as skill_catalog
from short_drama.agent.skill.runtime import resource_manifest_locked
from short_drama.agent.task.compaction import (
    schedule_task_compaction_locked,
    task_input_projection_locked,
)
from short_drama.agent.task.contract import reviewed_projection, source_projection
from short_drama.agent.task.queries import task_for_run
from short_drama.agent.tools.canvas import canvas_result_keys
from short_drama.agent.tools.image import (
    available_image_contents_locked,
    generated_image_sources,
    image_result_keys,
    image_sources,
)
from short_drama.agent.tools.registry import TOOL_VERSION, tool_catalog, tool_manifest
from short_drama.agent.tools.results import result_sources_locked
from short_drama.agent.tools.standard import standard_result_keys
from short_drama.ai.prompts.loader import load_prompt
from short_drama.ai.prompts.registry import PROMPT_VERSIONS, system_prompt
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall, AgentTurn
from short_drama.domain.assistant.tasks.task import AgentOperation
from short_drama.service.assistant.context.subject import selection_metadata
from short_drama.service.assistant.tasks.policy import generation_policy_locked

logger = logging.getLogger(__name__)
MAX_OPERATION_RECEIPTS = 64


def prepare_decision(session, conversation, run, settings=None):
    started = time.perf_counter()
    task = task_for_run(session, run)
    if task is None or task.conversation_id != conversation.id:
        raise AgentGatewayError("invalid_agent_checkpoint")
    if run.checkpoint.get("discussion_revision"):
        return _prepare_revision(session, conversation, run)
    schedule_task_compaction_locked(session, conversation, task, run)
    constraints, inputs, sources = task_input_projection_locked(session, conversation, task, run)
    metadata = selection_metadata(session, conversation.owner_user_id, task.context_snapshot)
    available = sorted(
        item["subject_key"] for item in metadata if item["availability"] != "unavailable"
    )
    attached = attachments_locked(session, conversation, task)
    restricted, _ = restricted_messages(session, conversation.id)
    state = deepcopy(task.effective_state)
    for activation in state.get("skill_activations", []):
        if int(activation["source_message_id"]) in restricted:
            activation["active"] = False
            activation["issue"] = "方法加载来源已被遗忘"
    task.effective_state = state
    attachment_ids = sorted(
        available_attachment_ids(
            session,
            conversation,
            task_attachments(session, task, restricted_ids=restricted),
        )
    )
    result_keys = sorted(
        standard_result_keys(session, conversation.owner_user_id, task)
        | canvas_result_keys(session, conversation.owner_user_id, task)
        | image_result_keys(session, conversation.owner_user_id, task)
    )
    image_reads = available_image_contents_locked(session, conversation, task, run)
    allowed_keys = (
        subject_memory_keys(
            {
                "schema_version": 2,
                "subjects": [
                    item
                    for item in snapshot_subjects(task.context_snapshot)
                    if item["subject_key"] in available
                ],
            }
        )
        | attachment_keys(attached)
        | set(result_keys)
    )
    policy = generation_policy_locked(session, conversation.owner_user_id)
    catalog = tool_catalog(task, media_enabled=policy.media_generation_enabled)
    tools = tool_manifest(task, media_enabled=policy.media_generation_enabled, progressive=True)
    checkpoint = deepcopy(run.checkpoint)
    scope = {
        "available_subject_keys": available,
        "available_attachment_ids": attachment_ids,
        "available_result_keys": result_keys,
        "available_image_content_keys": sorted(image_reads),
    }
    if checkpoint.get("memory_invalidated") or any(
        key in checkpoint and not set(checkpoint[key]).issubset(value)
        for key, value in scope.items()
    ):
        checkpoint["history_floor_turn_id"] = (
            session.scalar(
                select(AgentTurn.id)
                .where(AgentTurn.run_id.in_(select(AgentRun.id).where(AgentRun.task_id == task.id)))
                .order_by(AgentTurn.id.desc())
                .limit(1)
            )
            or 0
        )
    checkpoint.pop("memory_invalidated", None)
    checkpoint.update(
        scope,
        subject_scope_keys=sorted(allowed_keys),
        generation_policy_version=policy.policy_version,
        task_compaction_id=constraints["job_id"] if constraints else None,
        available_tool_names=[item["name"] for item in tools],
        permitted_tool_names=sorted(
            {item["name"] for item in tools}
            | {name for group in catalog for name in group["tools"]}
        ),
    )
    index = skill_catalog(session, conversation.owner_user_id, task)
    checkpoint["skill_index"] = index["items"]
    run.checkpoint = checkpoint
    state = deepcopy(task.effective_state)
    floor = checkpoint.get("history_floor_turn_id", 0)
    for key in ("media_evidence", "resource_outcomes"):
        state[key] = {
            identifier: item
            for identifier, item in state.get(key, {}).items()
            if int(item.get("source_turn_id") or item.get("turn_id", 0)) > floor
        }
    contract = state.get("task_contract")
    if contract:
        contract["requirements"] = [
            item
            for item in contract["requirements"]
            if int(item["source_message_id"]) not in restricted
        ]
        contract["reviewed_evidence"] = reviewed_projection(contract, floor)
    task.effective_state = state
    memory = assemble_memory_locked(
        session,
        conversation,
        task,
        run,
        represented_message_ids={str(row["id"]) for row in sources},
        allowed_subject_keys=allowed_keys,
    )
    # Select identities first; large tool bodies never enter the SQL sort.
    receipt_ids = session.scalars(
        select(AgentOperation.id)
        .join(AgentToolCall, AgentToolCall.id == AgentOperation.tool_call_id)
        .where(AgentOperation.task_id == task.id, AgentToolCall.turn_id > floor)
        .order_by(AgentOperation.id.desc())
        .limit(MAX_OPERATION_RECEIPTS)
    ).all()
    receipts = session.execute(
        select(
            AgentOperation.id,
            AgentOperation.tool_name,
            AgentOperation.status,
            AgentOperation.result_refs,
            AgentOperation.arguments["path"].as_string().label("path"),
            AgentOperation.arguments["offset"].as_integer().label("offset"),
            AgentOperation.result["next_offset"].as_integer().label("next_offset"),
            AgentOperation.result["already_loaded"].as_boolean().label("already_loaded"),
        ).where(AgentOperation.id.in_(receipt_ids))
    ).all()
    receipts = sorted(receipts, key=lambda item: item.id)
    intents = []
    pending = (run.checkpoint.get("pending_results") or {}).get("calls", {})
    for value in pending.values():
        if not isinstance(value, dict) or value.get("codec") != "agent.operation-ref":
            continue
        operation = session.scalar(
            select(AgentOperation).where(
                AgentOperation.id == int(value["operation_id"]),
                AgentOperation.task_id == task.id,
            )
        )
        result = operation.result or {} if operation else {}
        intent = result.get("hydration_intent")
        skill_available = False
        if intent and intent["resource"]["source"] == "skill_resource":
            values = ReadSkillResource.model_validate(operation.arguments)
            manifest = resource_manifest_locked(session, conversation, task, operation, values)
            expected = skill_resource_ref(
                manifest, f"skill-resource:{values.activation_id}:{values.path}"
            ).model_dump(mode="json")
            skill_available = intent["resource"] == expected
        if intent and (
            intent["resource"]["ref_id"] in attached
            or intent["resource"]["ref_id"] in {item["id"] for item in image_reads.values()}
            or skill_available
        ):
            intents.append(intent)
    frame = {
        "task_id": str(task.id),
        "goal": task.goal if not constraints else "根据约束摘要继续任务",
        "inputs": inputs,
        "task_constraints": constraints,
        "requirement_sources": source_projection(sources, restricted),
        "execution_target": task.execution_target,
        "script_reference": task.effective_state.get("script_reference"),
        "memory_project_ids": task.memory_project_ids,
        "memory": memory,
        "selected_subjects": metadata,
        "resources": [attachment_ref(item).model_dump(mode="json") for item in attached.values()],
        "image_sources": image_sources(task, set(available)),
        "image_results": generated_image_sources(session, conversation.owner_user_id, task),
        "skills": index,
        "active_skills": [activation_summary(item) for item in activations(task) if item["active"]],
        "generation_policy": policy.model_dump(mode="json"),
        "tool_catalog": catalog,
        "operation_receipts": [
            {
                "id": str(item.id),
                "tool_name": item.tool_name,
                "status": item.status,
                "result_refs": item.result_refs,
                "read_progress": {
                    "path": item.path,
                    "offset": item.offset or 0,
                    "next_offset": item.next_offset,
                }
                if item.path is not None
                else None,
                "already_loaded": item.already_loaded,
            }
            for item in receipts
        ],
        "result_sources": result_sources_locked(session, task, allowed_keys=allowed_keys),
        "tool_progress": {},
        "context_metrics": run.checkpoint.get("context_metrics", {}),
        "task_contract": task.effective_state.get("task_contract"),
        "working_set": working_projection(task, run),
        "media_evidence": task.effective_state.get("media_evidence", {}),
        "resource_outcomes": task.effective_state.get("resource_outcomes", {}),
        "progress": task.effective_state.get("progress", {}).get("feedback"),
        "completion_feedback": run.checkpoint.get("completion_feedback"),
    }
    profile = context_profile(run.config_snapshot)
    instructions = system_prompt("assistant")
    max_output = min(8192, profile["context_window_tokens"] // 8)
    static_tokens = (
        len(
            json.dumps(
                {"instructions": instructions, "context_frame": frame, "tools": tools},
                ensure_ascii=False,
            ).encode()
        )
        + 2
    ) // 3
    pinned_tokens = sum(item.get("body_tokens", 0) for item in activations(task) if item["active"])
    recent_tokens = max(
        0,
        profile["context_window_tokens"]
        - max_output
        - SAFETY_TOKENS
        - TOOL_RESULT_TOKENS
        - static_tokens
        - pinned_tokens,
    )
    history_manifest, tool_progress = freeze_transcript(
        session, run, task, recent_tokens=recent_tokens
    )
    frame["tool_progress"] = tool_progress
    frame["context_metrics"] = run.checkpoint.get("context_metrics", {})
    values = {
        "instructions": instructions,
        "user_prompt": "根据当前任务快照继续。",
        "continuation_prompt": load_prompt("assistant/continuation.md"),
        "context_frame": frame,
        "history_manifest": history_manifest,
        "hydration_intents": intents,
        "history": run.checkpoint.get("history"),
        "tools": tools,
        "stream": True,
        "max_output_tokens": max_output,
        "max_tool_calls": 16,
        "prompt_version": PROMPT_VERSIONS["assistant"],
        "tool_version": TOOL_VERSION,
        "harness_version": run.checkpoint["engine"],
        **profile,
    }
    values = prepare_review(session, task, run, values, settings)
    frame, tools = values["context_frame"], values["tools"]
    metrics = {
        **frame["context_metrics"],
        "assembly_ms": round((time.perf_counter() - started) * 1000, 2),
        "tool_count": len(tools),
        "tool_schema_bytes": len(
            json.dumps(tools, ensure_ascii=False, separators=(",", ":")).encode()
        ),
    }
    metrics["estimated_tool_tokens"] = (metrics["tool_schema_bytes"] + 2) // 3
    frame["context_metrics"] = metrics
    logger.info("agent_context_assembled", extra={"run_id": run.id, **metrics})
    assert_reference_payload(values)
    return values


def _prepare_revision(session, conversation, run):
    from short_drama.agent.task.discussion import prepare_revision

    values = prepare_revision(session, conversation, run)
    prompt = values["user_prompt"]
    # Revisions are bounded by their original position. Do not assemble today's
    # L4/L5 state, tool transcript or media pixels into an earlier discussion.
    values.update(
        instructions=system_prompt("assistant") + "\n" + values["instructions"],
        context_frame={
            "purpose": "discussion_revision",
            "prompt": prompt["content"] if isinstance(prompt, dict) else prompt,
            "resources": [
                attachment_ref(item).model_dump(mode="json")
                for item in prompt.get("attachments", [])
            ]
            if isinstance(prompt, dict)
            else [],
        },
        user_prompt="重新回答原位置的纯讨论。",
        history_manifest=[],
        hydration_intents=[],
        history=None,
        prompt_version=PROMPT_VERSIONS["assistant"],
        tool_version=TOOL_VERSION,
        harness_version=run.checkpoint["engine"],
    )
    assert_reference_payload(values)
    return values
