"""Bounded task constraints with immutable sources and a task-local parent CAS."""

import json
from copy import deepcopy

from sqlalchemy import select

from short_drama.agent.memory.sources import (
    MAX_SOURCE_BYTES,
    SOFT_HISTORY_BYTES,
    message_sources,
    source_hash,
    token_estimate,
)
from short_drama.domain.agent import AgentMessage
from short_drama.domain.agent_memory import AgentMemoryJob
from short_drama.domain.agent_task import AgentTask, AgentTaskInput
from short_drama.schemas.agent_memory import TaskConstraintResult
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id

RECENT_INPUT_BYTES = 8000
MAX_NEW_SOURCES = 128
COMPACTED_GOAL = "依据 task_constraints 和 inputs 中的近期原文继续当前任务，最新明确修正优先。"


def compacted_history(history, original_goal):
    if not history or history.get("codec") != "pydantic-ai.messages":
        return history
    result = deepcopy(history)
    for message in result["messages"]:
        for part in message.get("parts", []):
            if part.get("part_kind") != "user-prompt":
                continue
            content = part.get("content")
            if isinstance(content, str) and content == original_goal:
                part["content"] = COMPACTED_GOAL
            elif isinstance(content, list):
                part["content"] = [
                    COMPACTED_GOAL if isinstance(item, str) and item == original_goal else item
                    for item in content
                ]
    return result


def task_sources_locked(session, task, through_revision=None):
    session.flush()
    through = task.applied_input_revision if through_revision is None else through_revision
    inputs = session.execute(
        select(AgentTaskInput, AgentMessage.seq)
        .join(AgentMessage, AgentMessage.id == AgentTaskInput.message_id)
        .where(AgentTaskInput.task_id == task.id, AgentTaskInput.input_revision <= through)
        .order_by(AgentTaskInput.input_revision)
        .with_for_update()
    ).all()
    if not inputs:
        return []
    revisions = {str(item.message_id): item.input_revision for item, _seq in inputs}
    sources = message_sources(
        session,
        task.conversation_id,
        min(seq for _item, seq in inputs),
        max(seq for _item, seq in inputs),
        limit=None,
        message_ids=[item.message_id for item, _seq in inputs],
    )
    for row in sources:
        row["input_revision"] = revisions[row["id"]]
    return sorted(sources, key=lambda row: row["input_revision"])


def task_source_hash(sources):
    return digest(
        {"messages": source_hash(sources), "revisions": [row["input_revision"] for row in sources]}
    )


def active_compaction_locked(session, task, sources=None):
    state = task.effective_state.get("input_compaction")
    if not state:
        return None
    job = session.get(AgentMemoryJob, int(state["job_id"]))
    through = int(state["through_revision"])
    sources = task_sources_locked(session, task, through) if sources is None else sources
    prefix = [row for row in sources if row["input_revision"] <= through]
    if (
        job is None
        or job.kind != "task_state"
        or job.status != "succeeded"
        or job.conversation_id != task.conversation_id
        or job.source_snapshot.get("task_id") != str(task.id)
        or job.source_snapshot.get("through_revision") != through
        or len(prefix) != through
        or task_source_hash(prefix) != job.source_hash
    ):
        return None
    return state


def task_job_current_locked(session, conversation, job):
    snapshot = job.source_snapshot
    task = session.scalar(
        select(AgentTask)
        .where(
            AgentTask.id == int(snapshot["task_id"]),
            AgentTask.conversation_id == conversation.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or conversation.status != "active":
        return False
    through = snapshot["through_revision"]
    if through > task.applied_input_revision:
        return False
    sources = task_sources_locked(session, task, through)
    if len(sources) != through or task_source_hash(sources) != job.source_hash:
        return False
    current = active_compaction_locked(session, task)
    return (current["job_id"] if current else None) == snapshot["base_job_id"] and (
        current["through_revision"] if current else 0
    ) == snapshot["base_revision"]


def schedule_task_compaction_locked(session, conversation, task, run, sources=None):
    if conversation.status != "active":
        return None
    session.flush()
    sources = task_sources_locked(session, task) if sources is None else sources
    current = active_compaction_locked(session, task, sources)
    pending = session.scalars(
        select(AgentMemoryJob)
        .where(
            AgentMemoryJob.conversation_id == conversation.id,
            AgentMemoryJob.kind == "task_state",
            AgentMemoryJob.source_snapshot["task_id"].as_string() == str(task.id),
            AgentMemoryJob.status.in_(("pending", "running")),
        )
        .order_by(AgentMemoryJob.id)
        .with_for_update()
    ).all()
    for job in pending:
        if task_job_current_locked(session, conversation, job):
            return job
        job.status, job.message_status = "stale", "idle"
        job.lease_token = job.lease_until = None
        job.publish_token = job.publish_until = None
        job.finished_at = utcnow()
    base_revision = current["through_revision"] if current else 0
    recent = [row for row in sources if row["input_revision"] > base_revision]
    if (
        len(recent) < 2
        or len(json.dumps(recent, ensure_ascii=False).encode("utf-8")) < SOFT_HISTORY_BYTES
    ):
        return None
    # Retain at least the newest input, including a large correction, verbatim.
    cut, size = len(recent) - 1, len(json.dumps(recent[-1], ensure_ascii=False).encode("utf-8"))
    while cut > 0:
        amount = len(json.dumps(recent[cut - 1], ensure_ascii=False).encode("utf-8"))
        if size + amount > RECENT_INPUT_BYTES:
            break
        cut, size = cut - 1, size + amount
    messages, size = [], 0
    for row in recent[:cut]:
        amount = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        if size + amount > MAX_SOURCE_BYTES or len(messages) >= MAX_NEW_SOURCES:
            break
        messages.append(row)
        size += amount
    too_large = not messages
    messages = messages or recent[:1]
    through = messages[-1]["input_revision"]
    fingerprint = task_source_hash([row for row in sources if row["input_revision"] <= through])
    base_id = current["job_id"] if current else None
    key = digest({"task": str(task.id), "base": base_id, "through": through, "source": fingerprint})
    existing = session.scalar(
        select(AgentMemoryJob).where(
            AgentMemoryJob.owner_user_id == conversation.owner_user_id,
            AgentMemoryJob.request_key == key,
        )
    )
    if existing is not None:
        return existing
    now = utcnow()
    job = AgentMemoryJob(
        id=next_id(),
        owner_user_id=conversation.owner_user_id,
        conversation_id=conversation.id,
        run_id=run.id,
        kind="task_state",
        request_key=key,
        from_seq=messages[0]["seq"],
        to_seq=messages[-1]["seq"],
        base_summary_id=None,
        base_generation=0,
        source_hash=fingerprint,
        source_snapshot={
            "task_id": str(task.id),
            "base_job_id": base_id,
            "base_revision": base_revision,
            "through_revision": through,
            "messages": messages,
            "prior_facts": deepcopy(current["facts"]) if current else [],
            "model": deepcopy(run.config_snapshot),
        },
        status="failed" if too_large else "pending",
        provider_state="not_started",
        usage={},
        attempts=0,
        message_version=1,
        message_status="idle" if too_large else "queued",
        next_run_at=now,
        created_at=now,
        finished_at=now if too_large else None,
        error={"code": "agent_memory_source_too_large"} if too_large else None,
    )
    session.add(job)
    return job


def task_input_projection_locked(session, conversation, task, run):
    sources = task_sources_locked(session, task)
    current = active_compaction_locked(session, task, sources)
    schedule_task_compaction_locked(session, conversation, task, run, sources)
    through = current["through_revision"] if current else 0
    return (
        current,
        [
            {"id": row["id"], "content": row["content"]}
            for row in sources
            if row["input_revision"] > through
        ],
        sources,
    )


def task_compaction_waiting_locked(session, conversation, run):
    task = session.get(AgentTask, run.task_id)
    if task is None:
        return False
    job = schedule_task_compaction_locked(session, conversation, task, run)
    return job is not None and job.status in {"pending", "running"}


def settle_task_compaction_locked(session, conversation, job, payload, usage):
    if not task_job_current_locked(session, conversation, job):
        job.status = "stale"
        return False
    values = TaskConstraintResult.model_validate(payload)
    snapshot = job.source_snapshot
    messages = {row["id"]: row for row in snapshot["messages"]}
    prior = snapshot["prior_facts"]
    covered, covered_prior, facts = set(), set(), []
    for fact in values.facts:
        identifiers = [str(identifier) for identifier in fact.source_message_ids]
        indexes = fact.prior_fact_indexes
        if any(
            identifier not in messages
            or messages[identifier]["role"] != "user"
            or messages[identifier]["memory_restricted"]
            or not messages[identifier]["input_applied"]
            for identifier in identifiers
        ) or any(index >= len(prior) for index in indexes):
            raise ValueError("Task constraints cite an unavailable or unknown source")
        covered.update(identifiers)
        covered_prior.update(indexes)
        facts.append(
            {
                "text": fact.text,
                "source_message_ids": identifiers,
                "prior_fact_indexes": indexes,
                "source_job_id": snapshot["base_job_id"] if indexes else None,
            }
        )
    if any(
        row["role"] == "user"
        and row["input_applied"]
        and not row["memory_restricted"]
        and row["id"] not in covered
        for row in messages.values()
    ) or len(covered_prior) != len(prior):
        raise ValueError("Task constraints omitted a user input or a previous fact")
    if token_estimate(facts) > 5000:
        raise ValueError("Task constraints exceed their bounded input budget")
    task = session.get(AgentTask, int(snapshot["task_id"]))
    task.effective_state = {
        **task.effective_state,
        "input_compaction": {
            "job_id": str(job.id),
            "through_revision": snapshot["through_revision"],
            "facts": facts,
        },
    }
    task.row_version += 1
    task.updated_at = utcnow()
    job.status, job.usage, job.finished_at = "succeeded", usage, utcnow()
    return True
