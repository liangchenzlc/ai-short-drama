"""Agent limits and locked state transitions shared by API, tools and workers."""

import re
from copy import deepcopy
from datetime import datetime

from sqlalchemy import select

from short_drama.agent.core.events import ACTIVE as ACTIVE
from short_drama.agent.core.events import TERMINAL as TERMINAL
from short_drama.agent.core.events import append_event as append_event
from short_drama.agent.core.events import budget_limits as budget_limits
from short_drama.agent.core.events import initial_usage as initial_usage
from short_drama.agent.core.items import KIND_NARRATION, KIND_NOTICE, allocate_item_seq
from short_drama.domain.assistant.artifacts.models import AgentArtifact
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id


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


def _safe_context_budget(facts: object) -> dict:
    if not isinstance(facts, dict):
        return {}
    enumerated = {
        "phase": {"fixed", "post_summary", "main", "selector", "helper"},
        "source": {"assistant", "tool_selection", "summarization"},
        "fixed_kind": {
            "system_tools_and_pinned",
            "system_and_tools",
            "helper_prompt_and_prior_summary",
        },
    }
    result = {
        key: value
        for key, allowed in enumerated.items()
        if isinstance(value := facts.get(key), str) and value in allowed
    }
    for key in (
        "context_window_tokens",
        "input_limit_tokens",
        "reserved_output_tokens",
        "reserved_tool_tokens",
        "reserved_safety_tokens",
        "estimated_input_tokens",
        "fixed_estimated_input_tokens",
        "helper_input_limit_tokens",
        "message_count",
        "tool_call_count",
        "tool_result_count",
        "complete_tool_group_count",
        "covered_characters",
        "total_characters",
    ):
        value = facts.get(key)
        if type(value) is int and value >= 0:
            result[key] = value
    return result


def failure_message(code, *, accepted_unknown=False):
    """Stable user-facing explanations never reflect raw provider errors."""
    if code == "agent_response_timeout":
        return (
            "等待模型响应超时，本次执行已停止，未自动重发。"
            "请求可能已被受理，请先核对原调用；你的输入和已保存内容已保留。"
        )
    if accepted_unknown or code in {
        "agent_acceptance_unknown",
        "agent_provider_unknown",
        "incomplete_agent_response",
    }:
        return "请求可能已经提交，但结果暂时无法确认。请先核对当前任务，避免重复生成和计费。"
    messages = {
        "agent_connection_timeout": (
            "连接模型服务超时，已重试5次仍未能建立连接，请检查网络或模型服务后继续。"
            "本次请求尚未发出，你的输入已保留。"
        ),
        "agent_tool_choice_conflict": (
            "当前模型的思考模式与工具选择方式不兼容，请检查模型接口配置或更换模型后继续。"
            "你的输入已保留。"
        ),
        "assistant_engine_updated": "执行引擎已更新，请重新发送继续",
        "agent_execution_error": (
            "执行过程中发生系统错误，本次任务已停止。你的输入和已保存成果仍然保留，请联系维护人员排查。"
        ),
        "invalid_agent_tool_selection": (
            "模型选择了不可用的工具，本次任务已停止。已有输入和成果仍然保留，请调整要求或更换模型。"
        ),
        "agent_summary_empty": (
            "模型未能完成必要的上下文整理，本次任务已停止。原输入和已有成果仍然保留。"
        ),
        "agent_checkpoint_codec_unsupported": (
            "执行状态的存储格式暂不支持，请新建任务并联系维护人员排查。"
        ),
        "agent_checkpoint_corrupt": "执行状态数据无法读取，请新建任务并联系维护人员排查。",
        "agent_checkpoint_version_mismatch": (
            "执行引擎版本已变化，请新建任务后继续。已有成果仍然保留。"
        ),
        "agent_progress_stalled": (
            "连续尝试仍未取得新进展，已有成果和回执已保留。请补充缺少的资料或调整要求后继续。"
        ),
        "agent_completion_stalled": (
            "交付检查仍有未完成项，已有成果和检查反馈已保留。请核对缺项后继续。"
        ),
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
        "agent_reasoning_output_limit": (
            "模型已达到单次输出上限，仍未生成正文或完整工具调用。"
            "原思考设置与任务已保留，请调整任务要求或切换模型后继续。"
        ),
        "agent_output_no_progress": (
            "模型连续三轮未能产生新的完整输出，任务已暂停。已有成果已保留，请调整要求后继续。"
        ),
        "agent_tool_no_progress": (
            "模型连续三轮以相同参数重复失败的工具调用，任务已暂停。已有成果已保留，请处理错误后继续。"
        ),
        "agent_response_filtered": "模型服务过滤了本次响应，任务与输入已保留，请调整要求后继续。",
        "agent_budget_exhausted": "本次任务已达到执行上限，请缩小范围后继续。已有候选仍然保留。",
        "agent_context_budget_exhausted": (
            "任务输入、历史、方法或工具结果超过了本次上下文预算。"
            "原输入和完整回执已保留，请查看整理详情、缩小范围或拆分新任务后明确继续。"
        ),
        "agent_memory_waiting": ("会话历史正在后台整理，请稍后重新发送。原输入已保留。"),
        "agent_memory_maintenance_required": (
            "会话历史的后台整理尚未完成。请核对模型配置后再决定是否重试整理；"
            "若原模型已变化，请使用可用模型新建任务以恢复整理。原输入和来源已保留。"
        ),
        "agent_memory_acceptance_unknown": (
            "会话记忆整理的供应商受理结果不明。请先核对原调用，"
            "系统不会自动重复发送。原来源和输入已保留。"
        ),
        "agent_attachment_unavailable": (
            "当前任务的附件无法读取，请检查附件后继续。你的输入已保留。"
        ),
        "agent_source_changed": (
            "创作来源已经变化，请重新核对当前对象与要求后继续。已有候选仍然保留。"
        ),
        "agent_subject_unavailable": "当前创作对象已删除或脱离当前作品，请切换对象后继续。",
        "agent_task_not_executed": (
            "模型返回了文字说明，但尚未执行当前创作任务，也没有生成候选。"
            "可以换模型或调整要求后继续。"
        ),
        "agent_plan_not_completed": (
            "本次计划尚未执行完，已生成的候选仍然保留。请核对任务与候选后继续。"
        ),
        "agent_model_superseded": (
            "当前任务使用的模型配置在本任务开始后被修改，任务的模型快照已失效。"
            "请新建任务后重新发送，已有输入和候选仍然保留。"
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
            )
            .order_by(AgentArtifact.id)
        ).all()
    ]


def append_failure_message(session, conversation, run, error):
    message = AgentMessage(
        id=next_id(),
        conversation_id=conversation.id,
        seq=conversation.next_message_seq,
        item_seq=allocate_item_seq(conversation),
        role="assistant",
        kind=KIND_NOTICE,
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
        item_seq=allocate_item_seq(conversation),
        role="assistant",
        kind=KIND_NOTICE,
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


def append_interrupted_message(session, conversation, run, text):
    """Keep what the model already streamed when the user stops the run.

    The partial text is display-only: it never enters the protocol history, so the
    next run starts from the persisted checkpoint rather than a truncated segment.
    """
    content = (text or "").strip()[:4000]
    if not content:
        return None
    message = AgentMessage(
        id=next_id(),
        conversation_id=conversation.id,
        task_id=run.task_id,
        seq=conversation.next_message_seq,
        item_seq=allocate_item_seq(conversation),
        role="assistant",
        kind=KIND_NARRATION,
        content=content,
        references=[],
        artifacts=[],
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
            "item_seq": message.item_seq,
            "kind": KIND_NARRATION,
            "role": "assistant",
            "content": message.content,
            "artifacts": [],
        },
        run_id=run.id,
    )
    append_event(
        session,
        conversation,
        "run.interrupted",
        {"run_id": str(run.id), "preserved_chars": len(content)},
        run_id=run.id,
    )
    return message


def finish_locked(session, conversation, run, status, error=None):
    if status not in TERMINAL:
        raise ValueError("Invalid Agent terminal state")
    if run.status in TERMINAL:
        return False
    if status != "succeeded":
        from short_drama.dao.assistant.execution.model_ledger import AgentModelLedgerDAO

        # 停止和Worker失联也可能留下在途请求；新Run的确认门必须沿用账本事实。
        if AgentModelLedgerDAO(session, run.id).has_uncertain_request():
            error = {"code": "agent_acceptance_unknown", **(error or {}), "accepted_unknown": True}
    if getattr(run, "task_id", None) is not None:
        from short_drama.agent.task.state import finish_task_attempt_locked

        finish_task_attempt_locked(session, conversation, run, status, error)
    now = utcnow()
    charge_active(run, now)
    run.status = status
    run.checkpoint = {**run.checkpoint, "recovery_states": {}}
    run.error = safe_error(error.get("code")) if error else None
    if run.error and error.get("accepted_unknown") is True:
        run.error["accepted_unknown"] = True
    if run.error and run.error["code"] == "agent_context_budget_exhausted":
        facts = _safe_context_budget(error.get("context_budget"))
        if facts:
            run.error["context_budget"] = facts
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
    if getattr(run, "task_id", None) is not None and conversation.status == "active":
        from short_drama.agent.memory.context import schedule_maintenance_locked

        schedule_maintenance_locked(session, conversation, run)
    return True


def wait_locked(session, conversation, run, status, payload=None):
    if status not in {"waiting_generation", "waiting_input"}:
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
