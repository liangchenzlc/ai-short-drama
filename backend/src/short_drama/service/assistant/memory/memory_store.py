"""Explicit memory writes, bounded retrieval and provenance-aware forgetting."""

import json

from sqlalchemy import func, or_, select

from short_drama.agent.core import ENGINE_VERSION
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.assistant.memory.memory import AgentMemoryDAO, MemoryScopeContext
from short_drama.domain.assistant.conversations.models import AgentConversation, AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.memory.memory import (
    AgentConversationSummary,
    AgentMemoryForgetRule,
    AgentMemoryItem,
    AgentMemoryJob,
    AgentMemoryRevision,
    AgentMemorySource,
)
from short_drama.domain.assistant.operations.preference import AgentPreference
from short_drama.domain.assistant.tasks.task import AgentTask
from short_drama.schemas.assistant.memory.memory import (
    MemoryRead,
    normalize_memory_key,
)
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


def checked_scope(session, owner, conversation_id=None, *, allow_deleting=False):
    if conversation_id is not None:
        conversation = session.scalar(
            select(AgentConversation).where(
                AgentConversation.id == conversation_id,
                AgentConversation.owner_user_id == owner,
                AgentConversation.scope_version == 3,
                AgentConversation.status.in_(
                    ("active", "deleting") if allow_deleting else ("active",)
                ),
            )
        )
        if conversation is None:
            raise NotFound("记忆会话不存在或已停止使用")
    return MemoryScopeContext(owner)


def memory_capture_generation(session, owner: int, *, lock=False) -> int | None:
    query = select(
        AgentPreference.long_term_memory_enabled, AgentPreference.memory_generation
    ).where(AgentPreference.owner_user_id == owner)
    state = session.execute(query.with_for_update() if lock else query).one_or_none()
    return (state.memory_generation if state.long_term_memory_enabled else None) if state else 0


def require_memory_enabled(session, owner: int) -> int:
    generation = memory_capture_generation(session, owner)
    if generation is None:
        raise WorkflowError("assistant_memory_disabled", "长期记忆已关闭", 403)
    return generation


def extraction_generation_matches(session, job) -> bool:
    if job.kind != "extract":
        return True
    generation = memory_capture_generation(session, job.owner_user_id, lock=True)
    return generation is not None and generation == job.source_snapshot.get("memory_generation")


def memory_snapshot(row):
    return {
        name: getattr(row, name)
        for name in (
            "title",
            "content",
            "aliases",
            "tags",
            "pinned",
            "state",
            "origin",
            "kind",
            "memory_key",
        )
    }


def memory_read(session, row):
    return MemoryRead(
        id=row.id,
        kind=row.kind,
        memory_key=row.memory_key,
        title=row.title,
        content=row.content,
        aliases=row.aliases,
        tags=row.tags,
        pinned=bool(row.pinned),
        state=row.state,
        origin=row.origin,
        content_version=row.content_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def source_rows(session, owner, conversation_id, identifiers):
    if not identifiers:
        return []
    checked_scope(session, owner, conversation_id)
    rows = session.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == conversation_id, AgentMessage.id.in_(identifiers))
        .order_by(AgentMessage.seq)
    ).all()
    if {row.id for row in rows} != set(identifiers):
        raise NotFound("记忆来源消息不存在")
    return [
        {
            "hash": digest({"role": row.role, "content": row.content}),
        }
        for row in rows
    ]


def append_revision(session, row, change, sources=()):
    session.add(
        AgentMemoryRevision(
            id=next_id(),
            memory_id=row.id,
            content_version=row.content_version,
            change=change,
            snapshot=memory_snapshot(row),
            created_at=utcnow(),
        )
    )
    for fingerprint in dict.fromkeys(source["hash"] for source in sources):
        session.add(
            AgentMemorySource(
                id=next_id(),
                memory_id=row.id,
                content_version=row.content_version,
                source_hash=fingerprint,
                created_at=utcnow(),
            )
        )


def source_values(sources):
    return [
        {
            "hash": source.source_hash,
        }
        for source in sources
    ]


def lock_owner_conversations(session, owner, *, include_deleting=False):
    # Match the worker's Conversation -> memory lock order before editing a shared fact.
    return session.scalars(
        select(AgentConversation)
        .where(
            AgentConversation.owner_user_id == owner,
            AgentConversation.scope_version == 3,
            AgentConversation.status.in_(
                ("active", "deleting") if include_deleting else ("active",)
            ),
        )
        .order_by(AgentConversation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def search_text(row):
    return "\n".join([row.memory_key, row.title, *row.aliases, *row.tags, row.content])


def memory_job_read(job):
    hint = None
    if job.status == "failed":
        if job.provider_state in {"sending", "unknown"}:
            hint = "供应商受理结果不明，请先核对原调用；不会自动重发，也不能直接重试。"
        elif job.source_snapshot.get("retry_job_id"):
            hint = "已安排后续整理，请查看关联的重试记录。"
        elif (job.error or {}).get("code") == "invalid_agent_memory_result":
            hint = (
                "整理结果未通过内容或完整来源引用校验，原来源已保留。可重试此记录；"
                "若原模型已修改、停用或删除，请使用可用模型在同一会话新建任务以恢复整理。"
            )
        else:
            hint = (
                "原来源和调用记录已保留。请核对模型与上下文容量后重试；"
                "若原模型已失效，请使用可用模型在同一会话新建任务以恢复整理。"
            )
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "provider_state": job.provider_state,
        "message_version": str(job.message_version),
        "from_seq": job.from_seq,
        "to_seq": job.to_seq,
        "attempts": job.attempts,
        "error": {"code": job.error.get("code"), **({"message": hint} if hint else {})}
        if job.error
        else None,
        "usage": job.usage,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "retry_job_id": job.source_snapshot.get("retry_job_id"),
    }


def memory_summary_read(summary):
    return {
        "id": str(summary.id),
        "base_summary_id": str(summary.base_summary_id) if summary.base_summary_id else None,
        "from_seq": summary.from_seq,
        "to_seq": summary.to_seq,
        "facts": summary.content.get("facts", []),
        "token_estimate": summary.token_estimate,
        "created_at": summary.created_at,
    }


def blocked_extraction(session, owner, kind, key, sources):
    fingerprints = {source["hash"] for source in sources}
    rules = session.scalars(
        select(AgentMemoryForgetRule).where(
            AgentMemoryForgetRule.owner_user_id == owner, AgentMemoryForgetRule.active == 1
        )
    ).all()
    return any(
        (rule.kind, rule.memory_key) == (kind, key)
        or fingerprints.intersection(rule.source_fingerprints)
        for rule in rules
    )


def create_memory_locked(
    session,
    owner,
    values,
    *,
    key=None,
    request_hash=None,
    origin="explicit",
    sources=(),
    clarifications=None,
):
    existing = session.scalar(
        select(AgentMemoryItem)
        .where(
            AgentMemoryItem.owner_user_id == owner,
            AgentMemoryItem.kind == values.kind,
            AgentMemoryItem.memory_key == values.memory_key,
        )
        .with_for_update()
    )
    if origin == "extracted" and blocked_extraction(
        session, owner, values.kind, values.memory_key, sources
    ):
        return None
    if existing:
        if origin == "extracted":
            # Automatic candidates preserve corrections and conflicting facts.
            if existing.state != "active" or existing.content != values.content:
                if existing.state == "active" and clarifications is not None:
                    clarifications.append(
                        {
                            "memory_id": str(existing.id),
                            "content_version": str(existing.content_version),
                            "candidate": values.model_dump(mode="json"),
                            "source_message_ids": [str(source["message_id"]) for source in sources],
                            "status": "pending",
                        }
                    )
                return None
            prior = source_values(AgentMemoryDAO(session).sources(existing))
            identities = {source["hash"] for source in prior}
            additions = [source for source in sources if source["hash"] not in identities]
            if additions:
                existing.content_version += 1
                existing.updated_at = utcnow()
                append_revision(session, existing, "extract", [*prior, *additions])
            return existing
        raise Conflict("已有同一记忆，请编辑原条目；遗忘条目需明确恢复")
    now = utcnow()
    row = AgentMemoryItem(
        id=next_id(),
        owner_user_id=owner,
        kind=values.kind,
        memory_key=values.memory_key,
        title=values.title,
        content=values.content,
        aliases=values.aliases,
        tags=values.tags,
        content_version=1,
        state="active",
        origin=origin,
        pinned=int(values.pinned),
        last_user_edit_at=now if origin == "explicit" else None,
        hit_count=0,
        create_key=key,
        create_hash=request_hash,
        search_text="",
        created_at=now,
        updated_at=now,
    )
    row.search_text = search_text(row)
    session.add(row)
    session.flush()
    append_revision(session, row, "create" if origin == "explicit" else "extract", sources)
    return row


def invalidate_memory_runs(session, owner: int):
    latest_attempt = (
        select(func.max(AgentRun.id))
        .where(AgentRun.task_id == AgentTask.id)
        .correlate(AgentTask)
        .scalar_subquery()
    )
    runs = session.execute(
        select(AgentRun, AgentTask)
        .join(AgentTask, AgentTask.id == AgentRun.task_id)
        .where(
            AgentRun.initiated_by == owner,
            or_(
                AgentRun.status.in_(("queued", "running", "waiting_generation", "waiting_input")),
                # A paused Harness can receive a normal continuation message.
                # Its latest attempt must carry invalidation into that new Run.
                (AgentTask.status.in_(("paused_error", "paused_budget")))
                & (AgentTask.effective_state["engine"].as_string() == ENGINE_VERSION)
                & (AgentRun.id == latest_attempt),
            ),
        )
        .with_for_update()
    ).all()
    for run, _task in runs:
        checkpoint = dict(run.checkpoint or {})
        checkpoint["memory_epoch"] = checkpoint.get("memory_epoch", 0) + 1
        checkpoint["memory_invalidated"] = True
        run.checkpoint = checkpoint


def forget_memory_locked(session, row, *, change="forget"):
    if row.state == "forgotten":
        return
    sources = AgentMemoryDAO(session).sources(row)
    row.state = "forgotten"
    row.content_version += 1
    row.updated_at = utcnow()
    rule = session.scalar(
        select(AgentMemoryForgetRule)
        .where(AgentMemoryForgetRule.memory_id == row.id)
        .with_for_update()
    )
    if rule is None:
        rule = AgentMemoryForgetRule(
            id=next_id(),
            memory_id=row.id,
            owner_user_id=row.owner_user_id,
            kind=row.kind,
            memory_key=row.memory_key,
            forget_version=row.content_version,
            source_fingerprints=[],
            active=1,
            created_at=utcnow(),
        )
        session.add(rule)
    rule.forget_version = row.content_version
    rule.active = 1
    rule.restored_at = None
    rule.source_fingerprints = sorted(
        set(rule.source_fingerprints) | {source.source_hash for source in sources}
    )
    append_revision(session, row, change, source_values(sources))
    invalidate_memory_runs(session, row.owner_user_id)


def apply_memory_command_locked(session, row, action, content, new_sources):
    """Narrow tool mutations share revision, forgetting and invalidation primitives with the API."""
    if action == "forget":
        if row.state != "active":
            raise Conflict("此记忆当前不可遗忘，请先核对状态")
        forget_memory_locked(session, row)
        return
    sources = AgentMemoryDAO(session).sources(row)
    values = source_values(sources)
    identities = {source["hash"] for source in values}
    values.extend(source for source in new_sources if source["hash"] not in identities)
    if action == "update":
        if row.state != "active":
            raise Conflict("请先明确重新记住原条目，再修改内容")
        row.content = content
    elif action == "restore":
        if row.state != "forgotten":
            raise Conflict("此记忆尚未遗忘")
        row.state = "active"
        rule = session.scalar(
            select(AgentMemoryForgetRule)
            .where(AgentMemoryForgetRule.memory_id == row.id)
            .with_for_update()
        )
        if rule:
            rule.active = 0
            rule.restored_at = utcnow()
    else:
        raise WorkflowError("invalid_memory_action", "此记忆操作不受支持", 422)
    row.content_version += 1
    row.updated_at = row.last_user_edit_at = utcnow()
    row.origin = "explicit"
    row.search_text = search_text(row)
    append_revision(session, row, "edit" if action == "update" else "restore", values)
    session.flush()
    invalidate_memory_runs(session, row.owner_user_id)


def prepare_deletion_memory_locked(session, conversation):
    conversation.active_summary_id = None
    conversation.summary_covers_until_seq = 0
    conversation.summary_generation += 1
    for summary in session.scalars(
        select(AgentConversationSummary).where(
            AgentConversationSummary.conversation_id == conversation.id,
        )
    ).all():
        summary.status = "invalidated"
    for job in session.scalars(
        select(AgentMemoryJob)
        .where(
            AgentMemoryJob.conversation_id == conversation.id,
            AgentMemoryJob.kind != "purge",
            AgentMemoryJob.status.in_(("pending", "running")),
        )
        .with_for_update()
    ).all():
        job.status, job.message_status = "cancelled", "idle"
        job.lease_token = job.lease_until = None
        job.publish_token = job.publish_until = None
        job.error, job.finished_at = {"code": "conversation_unavailable"}, utcnow()


def memory_catalog_locked(session, scope, *, offset=0, limit=30, token_budget=1200):
    dao = AgentMemoryDAO(session)
    identities = session.execute(
        select(AgentMemoryItem.id, AgentMemoryItem.content_version, AgentMemoryItem.pinned)
        .where(dao.permitted(scope))
        .order_by(AgentMemoryItem.pinned.desc(), AgentMemoryItem.id)
        .with_for_update(read=True)
    ).all()
    version = digest(
        [[str(identifier), str(revision), pinned] for identifier, revision, pinned in identities]
    )
    entries = []
    used = 0
    rows = dao.catalog_rows(scope, offset=offset, limit=min(limit, 50))
    for row in rows:
        item = {
            "id": str(row.id),
            "kind": row.kind,
            "title": row.title,
            "content_version": str(row.content_version),
            "tags": row.tags[:4],
        }
        cost = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) // 3 + 1
        if used + cost > token_budget:
            break
        entries.append(item)
        used += cost
    return {
        "items": entries,
        "catalog_version": version,
        "offset": offset,
        "next_offset": offset + len(entries) if offset + len(entries) < len(identities) else None,
        "token_estimate": used,
        "total": len(identities),
    }


def search_memory_locked(session, scope, query, *, limit=12):
    query = normalize_memory_key(query)
    if not query or len(query) > 120:
        raise WorkflowError("invalid_memory_query", "记忆查询应包含 1–120 个字符", 422)
    dao = AgentMemoryDAO(session)
    exact = or_(
        AgentMemoryItem.memory_key == query,
        AgentMemoryItem.title == query,
        func.json_contains(AgentMemoryItem.aliases, json.dumps(query, ensure_ascii=False)) == 1,
        func.json_contains(AgentMemoryItem.tags, json.dumps(query, ensure_ascii=False)) == 1,
    )
    rows = session.scalars(
        dao.rows(scope)
        .where(exact)
        .order_by(AgentMemoryItem.pinned.desc(), AgentMemoryItem.id)
        .limit(limit)
    ).all()
    if len(rows) < limit:
        fulltext = AgentMemoryItem.search_text.match(query, mysql_boolean_mode=False)
        rows.extend(
            session.scalars(
                dao.rows(scope)
                .where(fulltext, AgentMemoryItem.id.not_in([row.id for row in rows]))
                .order_by(fulltext.desc(), AgentMemoryItem.id)
                .limit(limit - len(rows))
            ).all()
        )
    fallback = len(rows) < limit
    if fallback:
        # Bound short-word and proper-name fallback before applying substring matching.
        pool = (
            select(AgentMemoryItem.id)
            .where(dao.permitted(scope))
            .order_by(AgentMemoryItem.id.desc())
            .limit(500)
            .subquery()
        )
        rows.extend(
            session.scalars(
                dao.rows(scope)
                .where(
                    AgentMemoryItem.id.in_(select(pool.c.id)),
                    AgentMemoryItem.id.not_in([row.id for row in rows]),
                    AgentMemoryItem.search_text.contains(query, autoescape=True),
                )
                .order_by(AgentMemoryItem.id)
                .limit(limit - len(rows))
            ).all()
        )
    return {
        "items": [
            {
                "id": str(row.id),
                "title": row.title,
                "kind": row.kind,
                "content_version": str(row.content_version),
            }
            for row in rows
        ],
        "bounded_fallback": fallback,
        "fallback_candidate_limit": 500,
    }


def read_memories_locked(
    session, scope, identifiers, *, versions=None, offset=0, character_limit=4000
):
    dao = AgentMemoryDAO(session)
    rows = session.scalars(
        dao.rows(scope).where(AgentMemoryItem.id.in_(identifiers)).with_for_update()
    ).all()
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(identifiers):
        raise NotFound("记忆不在本任务允许范围内，或已遗忘")
    items = []
    remaining = character_limit
    for identifier in identifiers:
        row = by_id[identifier]
        if (
            versions
            and str(row.id) in versions
            and str(row.content_version) != str(versions[str(row.id)])
        ):
            raise Conflict("记忆内容已变化，请核对目录版本后读取")
        value = memory_read(session, row).model_dump(mode="json")
        value["content"] = row.content[offset : offset + remaining]
        value["range"] = {
            "offset": offset,
            "length": len(value["content"]),
            "total": len(row.content),
            "next_offset": offset + len(value["content"])
            if offset + len(value["content"]) < len(row.content)
            else None,
        }
        remaining -= len(value["content"])
        row.hit_count += 1
        row.last_used_at = utcnow()
        items.append(value)
    return {"items": items, "character_limit": character_limit}
