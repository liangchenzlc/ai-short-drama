"""Explicit memory writes, bounded retrieval and provenance-aware forgetting."""

import json

from sqlalchemy import func, or_, select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.agent_memory_dao import AgentMemoryDAO, MemoryScopeContext
from short_drama.domain import Project
from short_drama.domain.agent import AgentConversation, AgentMessage, AgentRun
from short_drama.domain.agent_memory import (
    AgentConversationSummary,
    AgentMemoryForgetRule,
    AgentMemoryItem,
    AgentMemoryJob,
    AgentMemoryRevision,
    AgentMemorySource,
)
from short_drama.domain.agent_task import AgentTask
from short_drama.schemas.agent_memory import (
    MemoryRead,
    normalize_memory_key,
)
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


def checked_scope(session, owner, conversation_id=None, project_ids=(), *, allow_deleting=False):
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
    projects = tuple(sorted(set(int(value) for value in project_ids)))
    if projects:
        found = set(
            session.scalars(
                select(Project.id).where(
                    Project.id.in_(projects),
                    Project.owner_user_id == owner,
                    Project.archived_at.is_(None),
                )
            ).all()
        )
        if found != set(projects):
            raise NotFound("作品记忆范围不存在或无权访问")
    return MemoryScopeContext(owner, conversation_id, projects)


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
            "scope_type",
            "scope_key",
            "kind",
            "memory_key",
        )
    }


def memory_read(session, row):
    return MemoryRead(
        id=row.id,
        scope_type=row.scope_type,
        conversation_id=row.conversation_id,
        project_id=row.project_id,
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
        sources=[
            {
                "conversation_id": str(source.conversation_id),
                "message_id": str(source.message_id) if source.message_id else None,
                "seq": source.source_seq,
                "subject_keys": source.subject_keys,
            }
            for source in AgentMemoryDAO(session).sources(row)
        ],
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
            "conversation_id": conversation_id,
            "message_id": row.id,
            "seq": row.seq,
            "hash": digest({"role": row.role, "content": row.content}),
            "subject_keys": [],
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
    for source in sources:
        session.add(
            AgentMemorySource(
                id=next_id(),
                memory_id=row.id,
                content_version=row.content_version,
                conversation_id=source["conversation_id"],
                message_id=source.get("message_id"),
                source_seq=source["seq"],
                source_hash=source["hash"],
                subject_keys=source.get("subject_keys", []),
                created_at=utcnow(),
            )
        )


def source_values(sources):
    return [
        {
            "conversation_id": source.conversation_id,
            "message_id": source.message_id,
            "seq": source.source_seq,
            "hash": source.source_hash,
            "subject_keys": source.subject_keys,
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
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "provider_state": job.provider_state,
        "message_version": str(job.message_version),
        "from_seq": job.from_seq,
        "to_seq": job.to_seq,
        "attempts": job.attempts,
        "error": job.error,
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


def blocked_extraction(session, owner, scope_type, scope_key, kind, key, sources):
    fingerprints = {source["hash"] for source in sources}
    rules = session.scalars(
        select(AgentMemoryForgetRule).where(
            AgentMemoryForgetRule.owner_user_id == owner, AgentMemoryForgetRule.active == 1
        )
    ).all()
    return any(
        (rule.scope_type, rule.scope_key, rule.kind, rule.memory_key)
        == (scope_type, scope_key, kind, key)
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
    scope_key = (
        "user"
        if values.scope_type == "user"
        else str(
            values.conversation_id if values.scope_type == "conversation" else values.project_id
        )
    )
    checked_scope(
        session, owner, values.conversation_id, (values.project_id,) if values.project_id else ()
    )
    existing = session.scalar(
        select(AgentMemoryItem)
        .where(
            AgentMemoryItem.owner_user_id == owner,
            AgentMemoryItem.scope_type == values.scope_type,
            AgentMemoryItem.scope_key == scope_key,
            AgentMemoryItem.kind == values.kind,
            AgentMemoryItem.memory_key == values.memory_key,
        )
        .with_for_update()
    )
    if origin == "extracted" and blocked_extraction(
        session, owner, values.scope_type, scope_key, values.kind, values.memory_key, sources
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
            identities = {(source["conversation_id"], source["seq"]) for source in prior}
            additions = [
                source
                for source in sources
                if (source["conversation_id"], source["seq"]) not in identities
            ]
            if additions:
                existing.content_version += 1
                existing.updated_at = utcnow()
                append_revision(session, existing, "extract", [*prior, *additions])
            return existing
        raise Conflict("此范围已有同一记忆，请编辑原条目；遗忘条目需明确恢复")
    now = utcnow()
    row = AgentMemoryItem(
        id=next_id(),
        owner_user_id=owner,
        scope_type=values.scope_type,
        scope_key=scope_key,
        conversation_id=values.conversation_id,
        project_id=values.project_id,
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


def invalidate_source_summaries(session, sources):
    identifiers = {source.conversation_id for source in sources}
    for identifier in sorted(identifiers):
        conversation = session.scalar(
            select(AgentConversation).where(AgentConversation.id == identifier).with_for_update()
        )
        if conversation is None:
            continue
        conversation.active_summary_id = None
        conversation.summary_covers_until_seq = 0
        conversation.summary_generation += 1
        for summary in session.scalars(
            select(AgentConversationSummary).where(
                AgentConversationSummary.conversation_id == identifier,
                AgentConversationSummary.status.in_(("active", "ready")),
            )
        ).all():
            summary.status = "invalidated"
    return identifiers


def invalidate_memory_runs(session, row, source_conversations=()):
    runs = session.execute(
        select(AgentRun, AgentTask)
        .join(AgentTask, AgentTask.id == AgentRun.task_id)
        .where(
            AgentRun.initiated_by == row.owner_user_id,
            AgentRun.status.in_(("queued", "running", "waiting_generation", "waiting_review")),
        )
        .with_for_update()
    ).all()
    for run, task in runs:
        affected = (
            run.conversation_id in source_conversations
            or row.scope_type == "user"
            or row.scope_type == "conversation"
            and run.conversation_id == row.conversation_id
            or row.scope_type == "project"
            and str(row.project_id) in task.memory_project_ids
        )
        if affected:
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
            scope_type=row.scope_type,
            scope_key=row.scope_key,
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
    invalidate_memory_runs(session, row, invalidate_source_summaries(session, sources))


def apply_memory_command_locked(session, row, action, content, new_sources):
    """Narrow tool mutations share revision, forgetting and invalidation primitives with the API."""
    if action == "forget":
        if row.state != "active":
            raise Conflict("此记忆当前不可遗忘，请先核对状态")
        forget_memory_locked(session, row)
        return
    sources = AgentMemoryDAO(session).sources(row)
    values = source_values(sources)
    identities = {(source["conversation_id"], source["seq"]) for source in values}
    values.extend(
        source
        for source in new_sources
        if (source["conversation_id"], source["seq"]) not in identities
    )
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
    invalidate_memory_runs(
        session, row, invalidate_source_summaries(session, AgentMemoryDAO(session).sources(row))
    )


def deletion_memory_query(conversation):
    related = (
        select(AgentMemorySource.id)
        .where(
            AgentMemorySource.memory_id == AgentMemoryItem.id,
            AgentMemorySource.content_version == AgentMemoryItem.content_version,
            AgentMemorySource.conversation_id == conversation.id,
        )
        .exists()
    )
    return select(AgentMemoryItem).where(
        AgentMemoryItem.owner_user_id == conversation.owner_user_id,
        AgentMemoryItem.scope_type.in_(("user", "project")),
        AgentMemoryItem.state == "active",
        related,
    )


def deletion_memory_read(session, row, conversation):
    reasons = []
    if row.last_user_edit_at is not None and row.last_user_edit_at > row.created_at:
        reasons.append("user_edited")
    if any(
        source.conversation_id != conversation.id for source in AgentMemoryDAO(session).sources(row)
    ):
        reasons.append("independent_sources")
    return {
        "id": str(row.id),
        "content_version": str(row.content_version),
        "scope_type": row.scope_type,
        "project_id": str(row.project_id) if row.project_id else None,
        "title": row.title,
        "content_preview": row.content[:500],
        "protection_reasons": reasons,
    }


def prepare_deletion_memory_locked(session, conversation, selections):
    # The caller locks the owner's conversations first, matching explicit memory edits.
    selected = {int(item["id"]): int(item["content_version"]) for item in selections}
    rows = (
        session.scalars(
            deletion_memory_query(conversation)
            .where(AgentMemoryItem.id.in_(selected))
            .order_by(AgentMemoryItem.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if selected
        else []
    )
    if {row.id for row in rows} != set(selected) or any(
        row.content_version != selected[row.id] for row in rows
    ):
        raise Conflict("待清理记忆已变化，请重新预览并明确选择；对话尚未删除")
    local = session.scalars(
        select(AgentMemoryItem)
        .where(
            AgentMemoryItem.owner_user_id == conversation.owner_user_id,
            AgentMemoryItem.conversation_id == conversation.id,
            AgentMemoryItem.state == "active",
        )
        .order_by(AgentMemoryItem.id)
        .with_for_update()
    ).all()
    for row in [*rows, *local]:
        forget_memory_locked(session, row, change="invalidate")
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
            "scope_type": row.scope_type,
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
                "scope_type": row.scope_type,
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
