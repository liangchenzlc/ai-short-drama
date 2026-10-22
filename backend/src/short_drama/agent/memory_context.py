"""L1/L2 projections, L4 compare-and-swap activation and reliable maintenance intents."""

import json
import re
from copy import deepcopy

from sqlalchemy import func, select

from short_drama.agent.attachment_context import historical_attachment_keys
from short_drama.agent.authorization import digest
from short_drama.agent.model_gateway import AgentGatewayError
from short_drama.agent.subject_context import subject_memory_keys
from short_drama.domain.agent import AgentConversation, AgentMessage, AgentRun
from short_drama.domain.agent_memory import (
    AgentConversationSummary,
    AgentMemoryForgetRule,
    AgentMemoryItem,
    AgentMemoryJob,
    AgentMemoryRevision,
    AgentMemorySource,
)
from short_drama.domain.agent_task import AgentOperation, AgentTask, AgentTaskInput
from short_drama.schemas.agent_memory import MemoryCreate, MemoryMaintenanceResult
from short_drama.service.agent_memory_service import (
    checked_scope,
    create_memory_locked,
    memory_catalog_locked,
)
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id

SOFT_HISTORY_BYTES = 16000
MAX_SOURCE_BYTES = 48000
HARD_CONTEXT_BYTES = 84000


def token_estimate(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8")) // 3 + 1


def selected_keys(task):
    return subject_memory_keys(task.context_snapshot)


def message_subject_keys(message, tasks, legacy_attachments=None):
    scopes = [ref for ref in message.references if ref.get("type") == "subject_scope"]
    if scopes:
        keys = {key for ref in scopes for key in ref.get("keys", [])}
        if not any(ref.get("includes_attachments") for ref in scopes):
            keys.update((legacy_attachments or {}).get(message.task_id, set()))
        return sorted(keys)
    if message.task_id not in tasks:
        return []
    task = tasks[message.task_id]
    return sorted(
        selected_keys(task)
        | set(task.effective_state.get("legacy_subject_keys", []))
        | (legacy_attachments or {}).get(message.task_id, set())
    )


def restricted_messages(session, conversation_id):
    conversation = session.get(AgentConversation, conversation_id)
    if conversation is None:
        return set(), set()
    sources = session.execute(
        select(AgentMemorySource.message_id, AgentMessage.task_id)
        .join(AgentMemoryItem, AgentMemoryItem.id == AgentMemorySource.memory_id)
        .join(
            AgentMemoryRevision,
            (AgentMemoryRevision.memory_id == AgentMemorySource.memory_id)
            & (AgentMemoryRevision.content_version == AgentMemorySource.content_version),
        )
        .outerjoin(AgentMessage, AgentMessage.id == AgentMemorySource.message_id)
        .where(
            AgentMemorySource.conversation_id == conversation_id,
            AgentMemoryItem.owner_user_id == conversation.owner_user_id,
            (AgentMemoryItem.state != "active")
            | (
                (AgentMemoryItem.origin == "explicit")
                & (AgentMemoryRevision.snapshot["content"].as_string() != AgentMemoryItem.content)
            ),
        )
        .with_for_update()
    ).all()
    return {row.message_id for row in sources}, {row.task_id for row in sources if row.task_id}


def message_sources(session, conversation_id, from_seq, to_seq, *, limit=500, message_ids=None):
    messages = session.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.conversation_id == conversation_id,
            AgentMessage.seq >= from_seq,
            AgentMessage.seq <= to_seq,
            AgentMessage.id.in_(message_ids) if message_ids is not None else True,
        )
        .order_by(AgentMessage.seq)
        .limit(limit)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    task_ids = {row.task_id for row in messages if row.task_id}
    tasks = (
        {
            row.id: row
            for row in session.scalars(
                select(AgentTask)
                .where(AgentTask.id.in_(task_ids))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        }
        if task_ids
        else {}
    )
    operations = (
        session.scalars(
            select(AgentOperation)
            .where(AgentOperation.task_id.in_(task_ids), AgentOperation.status == "succeeded")
            .with_for_update()
        ).all()
        if task_ids
        else []
    )
    explicit_memory_messages = {
        str(operation.arguments.get("request_message_id"))
        for operation in operations
        if operation.tool_name
        in {"remember_memory", "update_memory", "forget_memory", "restore_memory"}
    }
    inputs = (
        dict(
            session.execute(
                select(AgentTaskInput.message_id, AgentTaskInput.applied_plan_revision)
                .where(AgentTaskInput.message_id.in_([row.id for row in messages]))
                .with_for_update()
            ).all()
        )
        if messages
        else {}
    )
    suppressed_ids, suppressed_tasks = restricted_messages(session, conversation_id)
    legacy_attachments = historical_attachment_keys(session, task_ids)
    owner = (
        select(AgentConversation.owner_user_id)
        .where(AgentConversation.id == conversation_id)
        .scalar_subquery()
    )
    rules = session.scalars(
        select(AgentMemoryForgetRule)
        .where(AgentMemoryForgetRule.owner_user_id == owner, AgentMemoryForgetRule.active == 1)
        .with_for_update()
    ).all()
    fingerprints = {value for rule in rules for value in rule.source_fingerprints}
    suppressed_ids.update(
        row.id
        for row in messages
        if digest({"role": row.role, "content": row.content}) in fingerprints
    )
    suppressed_tasks.update(
        row.task_id for row in messages if row.id in suppressed_ids and row.task_id
    )
    return [
        {
            "id": str(row.id),
            "seq": row.seq,
            "role": row.role,
            "row_version": str(row.row_version),
            "content": "这条历史来源已被纠正或遗忘，已停止自动注入。"
            if row.id in suppressed_ids
            or row.role == "assistant"
            and row.task_id in suppressed_tasks
            else row.content,
            "memory_restricted": row.id in suppressed_ids
            or row.role == "assistant"
            and row.task_id in suppressed_tasks,
            "task_id": str(row.task_id) if row.task_id else None,
            "task_status": tasks[row.task_id].status if row.task_id in tasks else None,
            "input_applied": row.id not in inputs or inputs[row.id] is not None,
            "explicit_memory_processed": str(row.id) in explicit_memory_messages,
            "hash": digest({"role": row.role, "content": row.content}),
            "subject_keys": message_subject_keys(row, tasks, legacy_attachments)
            if row.role == "assistant" and row.task_id in tasks
            else [],
            "completed_operation_ids": [
                str(op.id)
                for op in operations
                if op.task_id == row.task_id
                and op.finished_at is not None
                and op.finished_at <= row.created_at
            ],
        }
        for row in messages
    ]


def source_hash(messages):
    return digest(
        [
            {
                key: row.get(key)
                for key in (
                    "id",
                    "seq",
                    "hash",
                    "row_version",
                    "memory_restricted",
                    "input_applied",
                    "explicit_memory_processed",
                )
            }
            for row in messages
        ]
    )


def enqueue_job_locked(session, conversation, run, kind, messages):
    if not messages:
        return None
    fingerprint = source_hash(messages)
    key = digest(
        {
            "conversation": str(conversation.id),
            "kind": kind,
            "from": messages[0]["seq"],
            "to": messages[-1]["seq"],
            "base": str(conversation.active_summary_id) if kind == "summary" else None,
            "generation": conversation.summary_generation if kind == "summary" else None,
            "source": fingerprint,
        }
    )
    existing = session.scalar(
        select(AgentMemoryJob).where(
            AgentMemoryJob.owner_user_id == conversation.owner_user_id,
            AgentMemoryJob.request_key == key,
        )
    )
    if existing is not None:
        return existing
    prior = (
        session.get(AgentConversationSummary, conversation.active_summary_id)
        if conversation.active_summary_id
        else None
    )
    now = utcnow()
    job = AgentMemoryJob(
        id=next_id(),
        owner_user_id=conversation.owner_user_id,
        conversation_id=conversation.id,
        run_id=run.id,
        kind=kind,
        request_key=key,
        from_seq=messages[0]["seq"],
        to_seq=messages[-1]["seq"],
        base_summary_id=conversation.active_summary_id,
        base_generation=conversation.summary_generation,
        source_hash=fingerprint,
        source_snapshot={
            "messages": messages,
            "prior_facts": deepcopy(prior.content.get("facts", [])) if prior else [],
            "model": deepcopy(run.config_snapshot),
        },
        status="pending",
        provider_state="not_started",
        usage={},
        attempts=0,
        message_version=1,
        message_status="queued",
        next_run_at=now,
        created_at=now,
    )
    session.add(job)
    return job


def bounded_prefix(messages):
    result = []
    total = 0
    for row in messages:
        size = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        if total + size > MAX_SOURCE_BYTES:
            break
        result.append(row)
        total += size
    return result


def enqueue_bounded_locked(session, conversation, run, kind, messages):
    if not messages:
        return None
    prefix = bounded_prefix(messages)
    job = enqueue_job_locked(session, conversation, run, kind, prefix or messages[:1])
    if not prefix:
        job.status = "failed"
        job.error = {"code": "agent_memory_source_too_large"}
        job.message_status = "idle"
        job.finished_at = utcnow()
    return job


def extraction_waterline(session, conversation_id):
    ranges = session.execute(
        select(AgentMemoryJob.from_seq, AgentMemoryJob.to_seq)
        .where(
            AgentMemoryJob.conversation_id == conversation_id,
            AgentMemoryJob.kind == "extract",
            AgentMemoryJob.status == "succeeded",
        )
        .order_by(AgentMemoryJob.from_seq, AgentMemoryJob.to_seq)
    ).all()
    covered = 0
    for start, end in ranges:
        if start > covered + 1:
            break
        covered = max(covered, end)
    return covered


def schedule_maintenance_locked(session, conversation, run):
    if run.task_id is None or conversation.scope_version != 3 or conversation.status != "active":
        return
    session.flush()
    latest = conversation.next_message_seq - 1
    active_seq = session.scalar(
        select(func.min(AgentMessage.seq))
        .join(AgentRun, AgentRun.trigger_message_id == AgentMessage.id)
        .where(
            AgentRun.conversation_id == conversation.id,
            AgentRun.status.in_(("queued", "running", "waiting_generation", "waiting_review")),
        )
    )
    latest = min(latest, active_seq - 1) if active_seq is not None else latest
    processed = extraction_waterline(session, conversation.id)
    blocking = session.scalar(
        select(AgentMemoryJob.id)
        .where(
            AgentMemoryJob.conversation_id == conversation.id,
            AgentMemoryJob.kind == "extract",
            AgentMemoryJob.from_seq <= processed + 1,
            AgentMemoryJob.to_seq >= processed + 1,
            AgentMemoryJob.status.in_(("pending", "running", "failed")),
        )
        .limit(1)
    )
    if latest > processed and blocking is None:
        rows = message_sources(session, conversation.id, processed + 1, latest)
        enqueue_bounded_locked(session, conversation, run, "extract", rows)
    pending_summary = session.scalar(
        select(AgentMemoryJob.id)
        .where(
            AgentMemoryJob.conversation_id == conversation.id,
            AgentMemoryJob.kind == "summary",
            AgentMemoryJob.status.in_(("pending", "running")),
            AgentMemoryJob.base_generation == conversation.summary_generation,
        )
        .limit(1)
    )
    if pending_summary is not None:
        return
    rows = message_sources(
        session, conversation.id, conversation.summary_covers_until_seq + 1, latest
    )
    # Retain recent messages and every still-open execution in L2.
    candidates = rows[:-8]
    if len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) >= SOFT_HISTORY_BYTES:
        enqueue_bounded_locked(session, conversation, run, "summary", candidates)


def validated_facts(job, values):
    messages = {row["id"]: row for row in job.source_snapshot["messages"]}
    prior = job.source_snapshot.get("prior_facts", [])
    prior_sources = {identifier for fact in prior for identifier in fact["source_message_ids"]}
    facts = []
    covered = set()
    covered_prior = set()
    for fact in values.facts:
        if any(
            str(identifier) not in messages and str(identifier) not in prior_sources
            for identifier in fact.source_message_ids
        ):
            raise ValueError("Summary cites a message outside its frozen sources")
        sources = [
            messages[str(identifier)]
            for identifier in fact.source_message_ids
            if str(identifier) in messages
        ]
        if any(row.get("memory_restricted") or not row["input_applied"] for row in sources):
            raise ValueError("Summary cannot restore a restricted or unapplied source")
        indexes = set(fact.prior_fact_indexes)
        # Old frozen provider receipts may cite previous message identities.
        # Convert them to the same bounded immutable parent references.
        identifiers = {str(identifier) for identifier in fact.source_message_ids}
        indexes.update(
            index
            for index, previous in enumerate(prior)
            if identifiers.intersection(previous["source_message_ids"])
        )
        if indexes and (
            job.base_summary_id is None or any(index >= len(prior) for index in indexes)
        ):
            raise ValueError("Summary cites a fact outside its previous summary")
        old = [prior[index] for index in sorted(indexes)]
        kind = fact.kind
        if kind == "user_fact" and (
            any(row["role"] != "user" for row in sources)
            or any(row["kind"] != "user_fact" for row in old)
        ):
            kind = "proposal"
        if kind == "completed" and not (
            any(row["completed_operation_ids"] for row in sources)
            or any(row["kind"] == "completed" for row in old)
        ):
            kind = "proposal"
        keys = sorted(
            {key for row in sources for key in row["subject_keys"]}
            | {key for row in old for key in row.get("subject_keys", [])}
        )
        facts.append(
            {
                "kind": kind,
                "text": fact.text,
                "source_message_ids": [row["id"] for row in sources],
                "prior_fact_indexes": sorted(indexes),
                "source_summary_id": str(job.base_summary_id) if indexes else None,
                "subject_keys": keys,
            }
        )
        covered.update(str(identifier) for identifier in fact.source_message_ids)
        covered_prior.update(indexes)
    if any(
        row["role"] == "user"
        and row["input_applied"]
        and not row.get("memory_restricted")
        and row["id"] not in covered
        for row in messages.values()
    ):
        raise ValueError("Summary omitted a user message from its source coverage")
    if len(covered_prior) != len(prior):
        raise ValueError("Summary omitted a previous summary fact")
    if token_estimate(facts) > 5000:
        raise ValueError("Summary exceeds its bounded phase budget")
    return facts


def settle_maintenance_locked(session, conversation, job, payload, usage):
    if job.status not in {"pending", "running"}:
        return False
    if conversation.status != "active":
        job.status = "cancelled"
        return False
    if job.kind == "task_state":
        from short_drama.agent.task_compaction import settle_task_compaction_locked

        return settle_task_compaction_locked(session, conversation, job, payload, usage)
    current = message_sources(session, conversation.id, job.from_seq, job.to_seq)
    if (
        source_hash(current) != job.source_hash
        or job.kind == "summary"
        and conversation.summary_generation != job.base_generation
    ):
        job.status = "stale"
        return False
    values = MemoryMaintenanceResult.model_validate(payload)
    if job.kind == "summary":
        if (
            conversation.active_summary_id != job.base_summary_id
            or conversation.summary_covers_until_seq + 1 != job.from_seq
        ):
            job.status = "stale"
            return False
        facts = validated_facts(job, values)
        summary = AgentConversationSummary(
            id=next_id(),
            owner_user_id=job.owner_user_id,
            conversation_id=conversation.id,
            from_seq=job.from_seq,
            to_seq=job.to_seq,
            base_summary_id=job.base_summary_id,
            base_generation=job.base_generation,
            source_hash=job.source_hash,
            content={"facts": facts},
            token_estimate=token_estimate(facts),
            model_config_id=job.source_snapshot["model"].get("id"),
            usage=usage,
            status="ready",
            created_at=utcnow(),
        )
        session.add(summary)
        session.flush()
        if conversation.active_summary_id:
            prior = session.get(AgentConversationSummary, conversation.active_summary_id)
            if prior:
                prior.status = "ready"
        summary.status = "active"
        summary.activated_at = utcnow()
        conversation.active_summary_id = summary.id
        conversation.summary_covers_until_seq = job.to_seq
        conversation.summary_generation += 1
    else:
        messages = {row["id"]: row for row in current}
        clarifications = []
        for candidate in values.memories:
            identifiers = [str(identifier) for identifier in candidate.source_message_ids]
            if any(
                identifier not in messages
                or messages[identifier]["role"] != "user"
                or messages[identifier].get("memory_restricted")
                or not messages[identifier]["input_applied"]
                for identifier in identifiers
            ):
                raise ValueError("Automatic memory must cite actual user messages")
            sources = [messages[identifier] for identifier in identifiers]
            if any(row.get("explicit_memory_processed") for row in sources):
                continue
            # Creative settings stay here; only explicit stable preferences cross chats.
            global_preference = candidate.kind == "preference" and any(
                re.search(r"以后|始终|一贯|长期|所有(作品|对话)|一直|每次", source["content"])
                for source in sources
            )
            candidate_values = MemoryCreate.model_validate(
                {
                    **candidate.model_dump(exclude={"source_message_ids"}),
                    "scope_type": "user" if global_preference else "conversation",
                    **({} if global_preference else {"conversation_id": str(conversation.id)}),
                }
            )
            create_memory_locked(
                session,
                job.owner_user_id,
                candidate_values,
                origin="extracted",
                clarifications=clarifications,
                sources=[
                    {
                        "conversation_id": conversation.id,
                        "message_id": int(row["id"]),
                        "seq": row["seq"],
                        "hash": row["hash"],
                        "subject_keys": [],
                    }
                    for row in sources
                ],
            )
        if clarifications:
            job.source_snapshot = {**job.source_snapshot, "clarifications": clarifications}
    job.status = "succeeded"
    job.usage = usage
    job.finished_at = utcnow()
    return True


def history_projection_locked(
    session, conversation, task, from_seq, to_seq, *, limit=20, allowed_subject_keys=None
):
    keys = selected_keys(task) if allowed_subject_keys is None else allowed_subject_keys
    page = message_sources(session, conversation.id, from_seq, to_seq, limit=limit + 1)
    rows = page[:limit]
    items = []
    for row in rows:
        if (
            not row["input_applied"]
            or row["task_id"] != str(task.id)
            and row["task_status"] in {"queued", "running", "waiting_media"}
        ):
            continue
        permitted = not row["memory_restricted"] and (
            not row["subject_keys"] or set(row["subject_keys"]).issubset(keys)
        )
        items.append(
            {
                "id": row["id"],
                "seq": row["seq"],
                "role": row["role"],
                "content": row["content"]
                if permitted or row["memory_restricted"]
                else "历史回复引用了未选入主体；本次未自动读取正文。",
                "source_restricted": not permitted,
                "operation_ids": row["completed_operation_ids"],
            }
        )
    return {"items": items, "next_seq": rows[-1]["seq"] + 1 if rows and len(page) > limit else None}


def assemble_memory_locked(
    session, conversation, task, run, *, represented_message_ids=(), allowed_subject_keys=None
):
    scope = checked_scope(
        session, conversation.owner_user_id, conversation.id, task.memory_project_ids
    )
    schedule_maintenance_locked(session, conversation, run)
    summary = (
        session.get(AgentConversationSummary, conversation.active_summary_id)
        if conversation.active_summary_id
        else None
    )
    keys = selected_keys(task) if allowed_subject_keys is None else allowed_subject_keys
    facts = [
        fact
        for fact in (
            summary.content.get("facts", []) if summary and summary.status == "active" else []
        )
        if set(fact.get("subject_keys", [])).issubset(keys)
    ]
    history = history_projection_locked(
        session,
        conversation,
        task,
        conversation.summary_covers_until_seq + 1,
        conversation.next_message_seq - 1,
        limit=500,
        allowed_subject_keys=keys,
    )
    result = {
        "catalog": memory_catalog_locked(session, scope),
        "summary": {
            "id": str(summary.id),
            "covers_until_seq": conversation.summary_covers_until_seq,
            "facts": facts,
        }
        if summary
        else None,
        "recent_messages": [
            item for item in history["items"] if item["id"] not in represented_message_ids
        ],
    }
    if (
        len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > HARD_CONTEXT_BYTES
        or history["next_seq"] is not None
    ):
        pending = session.scalar(
            select(AgentMemoryJob.id)
            .where(
                AgentMemoryJob.conversation_id == conversation.id,
                AgentMemoryJob.kind == "summary",
                AgentMemoryJob.status.in_(("pending", "running")),
            )
            .limit(1)
        )
        raise AgentGatewayError(
            "agent_memory_waiting" if pending else "agent_context_budget_exhausted"
        )
    return result
