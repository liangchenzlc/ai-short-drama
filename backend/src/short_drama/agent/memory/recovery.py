"""Recover failed maintenance from a new admitted run without losing source coverage."""

from copy import deepcopy

from sqlalchemy import BigInteger, and_, cast, or_, select
from sqlalchemy.orm import aliased

from short_drama.agent.core.access import model_unchanged
from short_drama.agent.memory.sources import message_sources, source_hash
from short_drama.core.time import utcnow
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.memory.memory import AgentMemoryJob
from short_drama.service.assistant.memory.memory_store import extraction_generation_matches
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id

INVALID_RESULTS = {
    "invalid_agent_memory_result",
    "invalid_agent_response",
    "invalid_agent_tool_calls",
    "agent_response_truncated",
}


def extraction_waterline(session, conversation_id):
    ranges = session.execute(
        select(AgentMemoryJob.from_seq, AgentMemoryJob.to_seq)
        .where(
            AgentMemoryJob.conversation_id == conversation_id,
            AgentMemoryJob.kind == "extract",
            AgentMemoryJob.status.in_(("succeeded", "abandoned")),
        )
        .order_by(AgentMemoryJob.from_seq, AgentMemoryJob.to_seq)
    ).all()
    covered = 0
    for start, end in ranges:
        if start > covered + 1:
            break
        covered = max(covered, end)
    return covered


def retry_child_locked(session, job, run, request_key, metadata):
    snapshot = {key: deepcopy(job.source_snapshot[key]) for key in ("messages", "prior_facts")}
    if job.kind == "extract":
        snapshot["memory_generation"] = job.source_snapshot["memory_generation"]
    snapshot.update(model=deepcopy(run.config_snapshot), manual_retry_of=str(job.id), **metadata)
    child = AgentMemoryJob(
        id=next_id(),
        owner_user_id=job.owner_user_id,
        conversation_id=job.conversation_id,
        run_id=run.id,
        kind=job.kind,
        request_key=request_key,
        from_seq=job.from_seq,
        to_seq=job.to_seq,
        base_summary_id=job.base_summary_id,
        base_generation=job.base_generation,
        source_hash=job.source_hash,
        source_snapshot=snapshot,
        status="pending",
        provider_state="not_started",
        usage={},
        attempts=0,
        message_version=1,
        message_status="queued",
        next_run_at=utcnow(),
        created_at=utcnow(),
    )
    session.add(child)
    job.source_snapshot = {**job.source_snapshot, "retry_job_id": str(child.id)}
    return child


def recover_failed_locked(session, conversation, run):
    if not model_unchanged(session, run):
        return
    processed = extraction_waterline(session, conversation.id)
    child = aliased(AgentMemoryJob)
    completed_retry = (
        select(child.id)
        .where(
            child.id
            == cast(AgentMemoryJob.source_snapshot["retry_job_id"].as_string(), BigInteger),
            child.conversation_id == AgentMemoryJob.conversation_id,
            child.kind == AgentMemoryJob.kind,
            child.from_seq == AgentMemoryJob.from_seq,
            child.to_seq == AgentMemoryJob.to_seq,
            child.status == "succeeded",
        )
        .exists()
    )
    covered = or_(
        and_(AgentMemoryJob.kind == "extract", AgentMemoryJob.to_seq <= processed),
        and_(
            AgentMemoryJob.kind == "summary",
            AgentMemoryJob.to_seq <= conversation.summary_covers_until_seq,
        ),
    )
    jobs = session.scalars(
        select(AgentMemoryJob)
        .where(
            AgentMemoryJob.conversation_id == conversation.id,
            AgentMemoryJob.status == "failed",
            AgentMemoryJob.kind.in_(("extract", "summary")),
            # 只略过成功 child 已连续覆盖的历史；当前缺口仍先校验来源，再检查 child。
            ~and_(covered, completed_retry),
        )
        .order_by(AgentMemoryJob.id)
        .with_for_update()
    ).all()
    for job in jobs:
        if not extraction_generation_matches(session, job):
            job.status = "abandoned"
            continue
        if job.provider_state in {"sending", "unknown"}:
            continue
        if (
            job.provider_state == "received"
            and (job.error or {}).get("code") not in INVALID_RESULTS
        ):
            continue
        current = message_sources(session, conversation.id, job.from_seq, job.to_seq)
        if (
            source_hash(current) != job.source_hash
            or job.kind == "summary"
            and (
                job.base_summary_id != conversation.active_summary_id
                or job.base_generation != conversation.summary_generation
                or job.from_seq != conversation.summary_covers_until_seq + 1
            )
        ):
            # 旧来源已撤回的确定失败不能继续挡住当前来源；原记录和回执保持可追溯。
            job.status = "stale"
            continue
        if job.source_snapshot.get("retry_job_id"):
            continue
        old_run = session.get(AgentRun, job.run_id) if job.run_id else None
        if old_run is not None and model_unchanged(session, old_run):
            continue
        retry_child_locked(
            session,
            job,
            run,
            digest({"retry_of": str(job.id), "run": str(run.id)}),
            {"rebound_from_run_id": str(job.run_id) if job.run_id else None},
        )
    session.flush()
