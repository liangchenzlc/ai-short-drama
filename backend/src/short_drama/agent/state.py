"""Agent limits and locked state transitions shared by API, tools and workers."""

import re
from copy import deepcopy
from datetime import datetime

from sqlalchemy import select

from short_drama.domain.agent import AgentArtifact, AgentEvent, AgentMessage, AgentToolCall
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE = frozenset({"queued", "running", "waiting_review", "waiting_generation"})


def budget_limits(mode="discuss", *, images=0, videos=0):
    workflow = mode == "workflow"
    return {
        "decision_calls": 16 if workflow else 8,
        "tool_calls": 32 if workflow else 16,
        "output_tokens_per_call": 8192,
        "output_tokens": 65536 if workflow else 32768,
        "active_seconds": 600 if workflow else 300,
        "images": images,
        "videos": videos,
    }


def initial_usage():
    return {
        "decision_calls": 0,
        "tool_calls": 0,
        "output_tokens": 0,
        "reserved_output_tokens": 0,
        "active_seconds": 0.0,
        "images": 0,
        "videos": 0,
    }


def append_event(session, conversation, event_type, payload, *, run_id=None):
    """Caller holds Project -> Conversation/Run; payload is display-safe data."""
    now = utcnow()
    row = AgentEvent(
        id=next_id(),
        conversation_id=conversation.id,
        run_id=run_id,
        seq=conversation.next_event_seq,
        event_type=event_type,
        payload=deepcopy(payload),
        created_at=now,
    )
    conversation.next_event_seq += 1
    conversation.updated_at = now
    session.add(row)
    return row


def charge_active(run, now=None):
    now = now or utcnow()
    checkpoint = deepcopy(run.checkpoint or {})
    runtime = checkpoint.setdefault("runtime", {})
    started = runtime.pop("active_since", None)
    if started is not None:
        try:
            elapsed = max(0.0, (now - datetime.fromisoformat(started)).total_seconds())
        except (TypeError, ValueError):
            elapsed = 0.0
        usage = {**initial_usage(), **(run.usage or {})}
        usage["active_seconds"] += elapsed
        run.usage = usage
    run.checkpoint = checkpoint


def mark_scheduled(run, *, phase="model", when=None):
    """Schedule only an explicitly resumed active Run; never create a new Run."""
    if run.status in TERMINAL or run.cancel_requested:
        return False
    now = utcnow()
    charge_active(run, now)
    run.status = "queued" if run.status == "queued" else "running"
    run.phase = phase
    run.next_run_at = when or now
    run.message_status = "pending"
    run.message_version += 1
    run.lease_token = run.lease_until = None
    run.updated_at = now
    run.row_version += 1
    return True


def safe_error(code):
    return {
        "code": code
        if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", code)
        else "agent_failed"
    }


def failure_message(code, *, accepted_unknown=False):
    """Stable user-facing explanations never reflect raw provider errors."""
    if accepted_unknown or code in {
        "agent_acceptance_unknown",
        "agent_provider_unknown",
        "incomplete_agent_response",
    }:
        return "请求可能已经提交，但结果暂时无法确认。请先核对当前任务，避免重复生成和计费。"
    messages = {
        "unsupported_agent_protocol": (
            "这个模型的接口暂时无法用于 Agent 创作，请换一个模型后继续。你的输入已保留。"
        ),
        "invalid_agent_configuration": (
            "这个模型的接口配置无法用于当前任务，请检查模型配置或换一个模型。你的输入已保留。"
        ),
        "agent_tools_unsupported": (
            "这个模型无法执行当前任务需要的工具，请换一个模型后继续。你的输入已保留。"
        ),
        "agent_image_input_unsupported": (
            "这个模型无法读取附件图片，请换一个支持图片理解的模型，或移除图片后继续。"
        ),
        "agent_audio_input_unsupported": (
            "这个模型无法理解附件中的声音，请换模型；视频也可以明确选择仅分析画面后继续。"
        ),
        "unsupported_agent_input_modality": (
            "当前模型接口无法读取附件中的声音或媒体格式，请换模型；视频可选择仅分析画面。"
        ),
        "agent_provider_authentication": (
            "模型服务拒绝了身份认证，请检查该模型的密钥配置。你的输入已保留。"
        ),
        "agent_provider_rate_limited": (
            "模型服务暂时限流或额度不足，请检查额度后再决定是否重试。你的输入已保留。"
        ),
        "agent_provider_rejected": (
            "模型服务拒绝了当前任务，请检查模型配置或调整要求后继续。你的输入已保留。"
        ),
        "agent_response_truncated": (
            "模型输出超过了长度限制，本次结果未完成。请缩小创作范围后继续。"
        ),
        "agent_budget_exhausted": "本次任务已达到执行上限，请缩小范围后继续。已有候选仍然保留。",
        "agent_attachment_unavailable": (
            "当前任务的附件无法读取，请检查附件后继续。你的输入已保留。"
        ),
        "agent_source_changed": (
            "创作来源已经变化，请重新核对当前对象与要求后继续。已有候选仍然保留。"
        ),
        "agent_subject_unavailable": "当前创作对象已删除或脱离本集，请切换对象后继续。",
        "agent_task_not_executed": (
            "模型返回了文字说明，但尚未执行当前创作任务，也没有生成候选。"
            "可以换模型或调整要求后继续。"
        ),
        "agent_plan_not_completed": (
            "本次计划尚未执行完，已生成的候选仍然保留。请核对任务与候选后继续。"
        ),
    }
    return messages.get(code, "当前任务未完成，请检查任务状态后继续。你的输入和已有候选仍然保留。")


def run_artifact_references(session, conversation, run):
    """Display-only references from the current private Run; never expose tool payloads."""
    return [
        {"artifact_id": str(artifact.id), "kind": artifact.kind}
        for artifact in session.scalars(
            select(AgentArtifact)
            .join(AgentToolCall, AgentToolCall.id == AgentArtifact.tool_call_id)
            .where(
                AgentToolCall.run_id == run.id,
                AgentArtifact.created_by == run.initiated_by,
                AgentArtifact.created_by == conversation.owner_user_id,
                AgentArtifact.project_id == conversation.project_id,
                AgentArtifact.episode_id == conversation.episode_id,
            )
            .order_by(AgentArtifact.id)
        ).all()
    ]


def append_failure_message(session, conversation, run, error):
    message = AgentMessage(
        id=next_id(),
        conversation_id=conversation.id,
        seq=conversation.next_message_seq,
        role="assistant",
        content=failure_message(
            error.get("code"), accepted_unknown=error.get("accepted_unknown", False)
        ),
        references=[],
        artifacts=run_artifact_references(session, conversation, run),
        created_at=utcnow(),
    )
    conversation.next_message_seq += 1
    session.add(message)
    append_event(
        session,
        conversation,
        "message.created",
        {
            "id": str(message.id),
            "run_id": str(run.id),
            "seq": message.seq,
            "role": "assistant",
            "content": message.content,
            "artifacts": message.artifacts,
        },
        run_id=run.id,
    )


def append_late_artifact_message(session, conversation, run, artifact_ids=None):
    """Keep terminal-run candidates reachable, linking each candidate only once."""
    references = [
        item
        for item in run_artifact_references(session, conversation, run)
        if artifact_ids is None or item["artifact_id"] in artifact_ids
    ]
    if not references:
        return
    # A candidate cannot be linked before its Run's triggering message. Bound
    # this lookup to later replies rather than replaying the conversation history.
    trigger_seq = (
        select(AgentMessage.seq)
        .where(
            AgentMessage.id == run.trigger_message_id,
            AgentMessage.conversation_id == conversation.id,
        )
        .correlate(None)
        .scalar_subquery()
    )
    # A collector may have opened a REPEATABLE READ snapshot before cancellation.
    # The Project/Conversation lock is held; read the latest committed replies.
    existing = {
        item.get("artifact_id")
        for artifacts in session.scalars(
            select(AgentMessage.artifacts)
            .where(
                AgentMessage.conversation_id == conversation.id,
                AgentMessage.role == "assistant",
                AgentMessage.seq > trigger_seq,
            )
            .with_for_update()
        ).all()
        for item in artifacts
        if isinstance(item, dict)
    }
    references = [item for item in references if item["artifact_id"] not in existing]
    if not references:
        return
    message = AgentMessage(
        id=next_id(),
        conversation_id=conversation.id,
        seq=conversation.next_message_seq,
        role="assistant",
        references=[],
        artifacts=references,
        created_at=utcnow(),
        content="此前已受理的制作任务已返回候选。本次 Agent 运行不会自动继续，请核对后手动采用。",
    )
    conversation.next_message_seq += 1
    session.add(message)
    append_event(
        session,
        conversation,
        "message.created",
        {
            "id": str(message.id),
            "run_id": str(run.id),
            "seq": message.seq,
            "role": "assistant",
            "content": message.content,
            "artifacts": references,
        },
        run_id=run.id,
    )


def finish_locked(session, conversation, run, status, error=None):
    if status not in TERMINAL:
        raise ValueError("Invalid Agent terminal state")
    if run.status in TERMINAL:
        return False
    now = utcnow()
    charge_active(run, now)
    run.status = status
    run.error = safe_error(error.get("code")) if error else None
    run.finished_at = now
    run.next_run_at = None
    run.message_status = "idle"
    run.message_version += 1
    run.lease_token = run.lease_until = None
    run.updated_at = now
    run.row_version += 1
    append_event(
        session,
        conversation,
        "run.finished",
        {
            "run_id": str(run.id),
            "status": status,
            "error": run.error,
        },
        run_id=run.id,
    )
    if status == "failed" and error:
        append_failure_message(session, conversation, run, error)
    elif status == "cancelled":
        append_late_artifact_message(session, conversation, run)
    return True


def wait_locked(session, conversation, run, status="waiting_review", payload=None):
    if status not in {"waiting_review", "waiting_generation"}:
        raise ValueError("Invalid Agent waiting state")
    if run.status in TERMINAL:
        return False
    now = utcnow()
    charge_active(run, now)
    run.status, run.phase = status, "wait"
    run.next_run_at = None
    run.message_status = "idle"
    run.message_version += 1
    run.lease_token = run.lease_until = None
    run.updated_at = now
    run.row_version += 1
    append_event(
        session,
        conversation,
        "run.waiting",
        {
            "run_id": str(run.id),
            "status": status,
            **(payload or {}),
        },
        run_id=run.id,
    )
    return True
