"""Explicit memory writes, bounded retrieval and provenance-aware forgetting."""

from copy import deepcopy

from sqlalchemy import func, select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.core.identity import require_actor
from short_drama.dao.agent_memory_dao import AgentMemoryDAO
from short_drama.domain.agent import AgentConversation, AgentRun
from short_drama.domain.agent_memory import (
    AgentConversationSummary,
    AgentMemoryForgetRule,
    AgentMemoryItem,
    AgentMemoryJob,
    AgentMemoryRevision,
)
from short_drama.schemas.agent_memory import (
    MemoryClarificationChoice,
    MemoryCreate,
    MemoryJobRetry,
    MemoryPatch,
    MemoryVersion,
    normalize_memory_key,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.agent_attachment_service import attachment_key
from short_drama.service.agent_memory_store import append_revision as append_revision
from short_drama.service.agent_memory_store import (
    apply_memory_command_locked as apply_memory_command_locked,
)
from short_drama.service.agent_memory_store import blocked_extraction as blocked_extraction
from short_drama.service.agent_memory_store import checked_scope as checked_scope
from short_drama.service.agent_memory_store import create_memory_locked as create_memory_locked
from short_drama.service.agent_memory_store import deletion_memory_query as deletion_memory_query
from short_drama.service.agent_memory_store import deletion_memory_read as deletion_memory_read
from short_drama.service.agent_memory_store import forget_memory_locked as forget_memory_locked
from short_drama.service.agent_memory_store import invalidate_memory_runs as invalidate_memory_runs
from short_drama.service.agent_memory_store import (
    invalidate_source_summaries as invalidate_source_summaries,
)
from short_drama.service.agent_memory_store import (
    lock_owner_conversations as lock_owner_conversations,
)
from short_drama.service.agent_memory_store import memory_catalog_locked as memory_catalog_locked
from short_drama.service.agent_memory_store import memory_job_read as memory_job_read
from short_drama.service.agent_memory_store import memory_read as memory_read
from short_drama.service.agent_memory_store import memory_snapshot as memory_snapshot
from short_drama.service.agent_memory_store import memory_summary_read as memory_summary_read
from short_drama.service.agent_memory_store import (
    prepare_deletion_memory_locked as prepare_deletion_memory_locked,
)
from short_drama.service.agent_memory_store import read_memories_locked as read_memories_locked
from short_drama.service.agent_memory_store import search_memory_locked as search_memory_locked
from short_drama.service.agent_memory_store import search_text as search_text
from short_drama.service.agent_memory_store import source_rows as source_rows
from short_drama.service.agent_memory_store import source_values as source_values
from short_drama.service.base import BaseService, Page, utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


class AgentMemoryService(BaseService):
    model = AgentMemoryItem

    def _owner(self):
        return require_actor(self.session).user_id

    def list_items(
        self,
        *,
        conversation_id=None,
        project_id=None,
        query=None,
        offset=0,
        limit=30,
        include_forgotten=False,
    ):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            scope = checked_scope(
                self.session,
                self._owner(),
                parse_identifier(conversation_id) if conversation_id else None,
                (parse_identifier(project_id),) if project_id else (),
            )
            dao = AgentMemoryDAO(self.session)
            statement = dao.rows(scope, active=not include_forgotten)
            if query:
                statement = statement.where(
                    AgentMemoryItem.search_text.contains(query.strip(), autoescape=True)
                )
            rows = self.session.scalars(
                statement.order_by(AgentMemoryItem.pinned.desc(), AgentMemoryItem.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return Page(
                items=[memory_read(self.session, row) for row in rows],
                total=self.session.scalar(select(func.count()).select_from(statement.subquery())),
                offset=offset,
                limit=limit,
            )

    def get_item(self, identifier):
        with self._transaction(read_only=True):
            row = AgentMemoryDAO(self.session).item(parse_identifier(identifier), self._owner())
            if row is None:
                raise NotFound("记忆不存在")
            return memory_read(self.session, row)

    def conversation_context(self, identifier):
        with self._transaction(read_only=True):
            identifier = parse_identifier(identifier)
            owner = self._owner()
            checked_scope(self.session, owner, identifier)
            conversation = self.session.get(AgentConversation, identifier)
            summary = (
                self.session.get(AgentConversationSummary, conversation.active_summary_id)
                if conversation.active_summary_id
                else None
            )
            jobs = self.session.scalars(
                select(AgentMemoryJob)
                .where(
                    AgentMemoryJob.owner_user_id == owner,
                    AgentMemoryJob.conversation_id == identifier,
                )
                .order_by(AgentMemoryJob.id.desc())
                .limit(20)
            ).all()
            return {
                "conversation_id": str(identifier),
                "covers_until_seq": conversation.summary_covers_until_seq,
                "summary_generation": str(conversation.summary_generation),
                "summary": memory_summary_read(summary)
                if summary and summary.status == "active"
                else None,
                "jobs": [memory_job_read(job) for job in jobs],
            }

    def read_summary(self, conversation_id, summary_id):
        with self._transaction(read_only=True):
            owner = self._owner()
            conversation_id = parse_identifier(conversation_id)
            checked_scope(self.session, owner, conversation_id)
            summary = self.session.scalar(
                select(AgentConversationSummary).where(
                    AgentConversationSummary.id == parse_identifier(summary_id),
                    AgentConversationSummary.conversation_id == conversation_id,
                    AgentConversationSummary.owner_user_id == owner,
                    AgentConversationSummary.status.in_(("active", "ready")),
                )
            )
            if summary is None:
                raise NotFound("摘要不存在或来源已停止使用")
            return memory_summary_read(summary)

    def retry_job(self, identifier, payload, idempotency_key):
        from short_drama.agent.access import model_unchanged
        from short_drama.agent.memory_sources import message_sources, source_hash

        values = self._payload(MemoryJobRetry, payload)
        key = attachment_key(idempotency_key)
        request_hash = digest(values)
        with self._transaction():
            owner = self._owner()
            lock_owner_conversations(self.session, owner)
            job = self.session.scalar(
                select(AgentMemoryJob)
                .where(
                    AgentMemoryJob.id == parse_identifier(identifier),
                    AgentMemoryJob.owner_user_id == owner,
                    AgentMemoryJob.kind.in_(("summary", "extract", "task_state")),
                )
                .with_for_update()
            )
            if job is None:
                raise NotFound("记忆整理记录不存在")
            checked_scope(self.session, owner, job.conversation_id)
            retry_key = digest({"retry_of": str(job.id), "key": key})
            prior = self.session.scalar(
                select(AgentMemoryJob).where(
                    AgentMemoryJob.owner_user_id == owner,
                    AgentMemoryJob.request_key == retry_key,
                )
            )
            if prior is not None:
                if prior.source_snapshot.get("retry_request_hash") != request_hash:
                    raise Conflict("此重试请求身份已用于其他版本")
                return memory_job_read(prior)
            if job.message_version != values["message_version"] or job.status != "failed":
                raise Conflict("整理记录已变化或尚未失败，请刷新详情")
            if job.provider_state in {"sending", "unknown"}:
                raise Conflict("供应商受理结果不明，不能重复发送，请先核对原调用")
            if job.source_snapshot.get("retry_job_id"):
                raise Conflict("这条记录已安排重试，请刷新查看后续整理记录")
            conversation = self.session.get(AgentConversation, job.conversation_id)
            if job.kind == "task_state":
                from short_drama.agent.task_compaction import task_job_current_locked

                current_source = task_job_current_locked(self.session, conversation, job)
            else:
                current_source = (
                    source_hash(
                        message_sources(self.session, job.conversation_id, job.from_seq, job.to_seq)
                    )
                    == job.source_hash
                )
            if (
                not current_source
                or job.kind == "summary"
                and (
                    conversation.active_summary_id != job.base_summary_id
                    or conversation.summary_generation != job.base_generation
                    or conversation.summary_covers_until_seq + 1 != job.from_seq
                )
            ):
                raise Conflict("整理来源或摘要版本已变化，请保留当前结果并等待新的整理")
            run = self.session.get(AgentRun, job.run_id) if job.run_id else None
            if run is None or not model_unchanged(self.session, run):
                raise Conflict("原模型配置已变化，请通过新的任务使用当前模型")
            fields = ["messages", "prior_facts", "model"]
            if job.kind == "task_state":
                fields.extend(["task_id", "base_job_id", "base_revision", "through_revision"])
            snapshot = {field: deepcopy(job.source_snapshot[field]) for field in fields}
            snapshot.update(manual_retry_of=str(job.id), retry_request_hash=request_hash)
            child = AgentMemoryJob(
                id=next_id(),
                owner_user_id=owner,
                conversation_id=job.conversation_id,
                run_id=job.run_id,
                kind=job.kind,
                request_key=retry_key,
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
            self.session.add(child)
            job.source_snapshot = {**job.source_snapshot, "retry_job_id": str(child.id)}
            self.session.flush()
            return memory_job_read(child)

    def _review_read(self, job):
        from short_drama.agent.memory_sources import message_sources, source_hash

        valid_source = (
            source_hash(
                message_sources(self.session, job.conversation_id, job.from_seq, job.to_seq)
            )
            == job.source_hash
        )
        clarifications = []
        for index, candidate in enumerate(job.source_snapshot.get("clarifications", [])):
            row = AgentMemoryDAO(self.session).item(int(candidate["memory_id"]), self._owner())
            available = row is not None and row.state == "active" and valid_source
            status = (
                "resolved"
                if candidate["status"] == "resolved"
                else "unavailable"
                if not available
                else "changed"
                if str(row.content_version) != candidate["content_version"]
                else "pending"
            )
            clarifications.append(
                {
                    "index": index,
                    "memory_id": candidate["memory_id"],
                    "content_version": str(row.content_version)
                    if row
                    else candidate["content_version"],
                    "candidate_base_version": candidate["content_version"],
                    "title": row.title if available else "原记忆或候选来源已停止使用",
                    "current_content": row.content if available else None,
                    "candidate_content": candidate["candidate"]["content"] if available else None,
                    "source_message_ids": candidate["source_message_ids"] if available else [],
                    "status": status,
                    "choice": candidate.get("choice"),
                }
            )
        return {
            "id": str(job.id),
            "conversation_id": str(job.conversation_id),
            "from_seq": job.from_seq,
            "to_seq": job.to_seq,
            "clarifications": clarifications,
        }

    def review_jobs(self, identifier, offset=0, limit=20):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            identifier = parse_identifier(identifier)
            checked_scope(self.session, self._owner(), identifier)
            statement = select(AgentMemoryJob).where(
                AgentMemoryJob.owner_user_id == self._owner(),
                AgentMemoryJob.conversation_id == identifier,
                AgentMemoryJob.kind == "extract",
                AgentMemoryJob.status == "succeeded",
                func.json_length(AgentMemoryJob.source_snapshot["clarifications"]) > 0,
            )
            total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
            jobs = self.session.scalars(
                statement.order_by(AgentMemoryJob.id.desc()).offset(offset).limit(limit)
            ).all()
            return Page(
                items=[self._review_read(job) for job in jobs],
                total=total,
                offset=offset,
                limit=limit,
            )

    def resolve_clarification(self, identifier, index, payload, idempotency_key):
        from short_drama.agent.memory_sources import message_sources, source_hash

        values = self._payload(MemoryClarificationChoice, payload)
        key, request_hash = attachment_key(idempotency_key), digest(values)
        with self._transaction():
            owner = self._owner()
            lock_owner_conversations(self.session, owner)
            job = self.session.scalar(
                select(AgentMemoryJob)
                .where(
                    AgentMemoryJob.id == parse_identifier(identifier),
                    AgentMemoryJob.owner_user_id == owner,
                    AgentMemoryJob.kind == "extract",
                    AgentMemoryJob.status == "succeeded",
                )
                .with_for_update()
            )
            if job is None:
                raise NotFound("记忆整理记录不存在")
            checked_scope(self.session, owner, job.conversation_id)
            snapshot = deepcopy(job.source_snapshot)
            candidates = snapshot.get("clarifications", [])
            if type(index) is not int or not 0 <= index < len(candidates):
                raise NotFound("记忆澄清候选不存在")
            candidate = candidates[index]
            if candidate.get("request_key") == key:
                if candidate.get("request_hash") != request_hash:
                    raise Conflict("此请求身份已用于其他澄清选择")
                return self._review_read(job)
            if candidate["status"] != "pending":
                raise Conflict("此候选已经处理，请刷新查看结果")
            row = AgentMemoryDAO(self.session).item(int(candidate["memory_id"]), owner, lock=True)
            if row is None or row.state != "active":
                raise Conflict("原记忆已停止使用，不会通过旧候选恢复")
            if row.content_version != values["content_version"]:
                raise Conflict("原记忆已变化，请刷新并对照新版本后选择")
            if values["choice"] == "use_candidate":
                current = message_sources(
                    self.session, job.conversation_id, job.from_seq, job.to_seq
                )
                if source_hash(current) != job.source_hash:
                    raise Conflict("候选来源已纠正或遗忘，请保留当前记忆")
                incoming = MemoryCreate.model_validate(candidate["candidate"])
                if (row.scope_type, row.kind, row.memory_key) != (
                    incoming.scope_type,
                    incoming.kind,
                    incoming.memory_key,
                ):
                    raise Conflict("候选身份与当前事实不一致")
                sources = source_values(AgentMemoryDAO(self.session).sources(row))
                additions = source_rows(
                    self.session,
                    owner,
                    job.conversation_id,
                    [int(value) for value in candidate["source_message_ids"]],
                )
                identities = {(item["conversation_id"], item["seq"]) for item in sources}
                sources.extend(
                    item
                    for item in additions
                    if (item["conversation_id"], item["seq"]) not in identities
                )
                row.content, row.title, row.aliases, row.tags = (
                    incoming.content,
                    incoming.title,
                    incoming.aliases,
                    incoming.tags,
                )
                row.content_version += 1
                row.updated_at = row.last_user_edit_at = utcnow()
                row.origin, row.search_text = "explicit", search_text(row)
                append_revision(self.session, row, "edit", sources)
                self.session.flush()
                invalidate_memory_runs(
                    self.session,
                    row,
                    invalidate_source_summaries(
                        self.session, AgentMemoryDAO(self.session).sources(row)
                    ),
                )
            candidate.update(
                status="resolved",
                choice=values["choice"],
                request_key=key,
                request_hash=request_hash,
            )
            job.source_snapshot = snapshot
            self.session.flush()
            return self._review_read(job)

    def create_item(self, payload, idempotency_key):
        values = MemoryCreate.model_validate(self._payload(MemoryCreate, payload))
        key = attachment_key(idempotency_key)
        request_hash = digest(values.model_dump(mode="json"))
        with self._transaction():
            owner = self._owner()
            lock_owner_conversations(self.session, owner)
            prior = self.session.scalar(
                select(AgentMemoryItem)
                .where(AgentMemoryItem.owner_user_id == owner, AgentMemoryItem.create_key == key)
                .with_for_update()
            )
            if prior:
                if prior.create_hash != request_hash:
                    raise Conflict("此请求身份已用于其他记忆")
                if prior.conversation_id is not None:
                    checked_scope(self.session, owner, prior.conversation_id)
                return memory_read(self.session, prior)
            sources = source_rows(
                self.session, owner, values.source_conversation_id, values.source_message_ids
            )
            row = create_memory_locked(
                self.session, owner, values, key=key, request_hash=request_hash, sources=sources
            )
            self.session.flush()
            return memory_read(self.session, row)

    def _versioned(self, identifier, version):
        lock_owner_conversations(self.session, self._owner())
        row = AgentMemoryDAO(self.session).item(
            parse_identifier(identifier), self._owner(), lock=True
        )
        if row is None:
            raise NotFound("记忆不存在")
        if row.content_version != version:
            raise Conflict("记忆已变化，草稿已保留，请核对新版本")
        return row

    def patch_item(self, identifier, payload):
        values = self._payload(MemoryPatch, payload)
        with self._transaction():
            row = self._versioned(identifier, values.pop("content_version"))
            if row.state != "active":
                raise Conflict("请先明确恢复这条记忆后再编辑")
            sources = AgentMemoryDAO(self.session).sources(row)
            for key, value in values.items():
                setattr(
                    row,
                    key,
                    list(dict.fromkeys(normalize_memory_key(label) for label in value))
                    if key in {"aliases", "tags"}
                    else int(value)
                    if key == "pinned"
                    else value.strip(),
                )
            row.content_version += 1
            row.updated_at = row.last_user_edit_at = utcnow()
            row.origin = "explicit"
            row.search_text = search_text(row)
            append_revision(self.session, row, "edit", source_values(sources))
            if "content" in values:
                invalidate_memory_runs(
                    self.session, row, invalidate_source_summaries(self.session, sources)
                )
            self.session.flush()
            return memory_read(self.session, row)

    def forget_item(self, identifier, payload):
        values = self._payload(MemoryVersion, payload)
        with self._transaction():
            row = self._versioned(identifier, values["content_version"])
            forget_memory_locked(self.session, row)
            self.session.flush()
            return memory_read(self.session, row)

    def restore_item(self, identifier, payload):
        values = self._payload(MemoryVersion, payload)
        with self._transaction():
            row = self._versioned(identifier, values["content_version"])
            if row.state != "forgotten":
                raise Conflict("此记忆尚未遗忘")
            sources = AgentMemoryDAO(self.session).sources(row)
            row.state = "active"
            row.content_version += 1
            row.updated_at = row.last_user_edit_at = utcnow()
            row.origin = "explicit"
            rule = self.session.scalar(
                select(AgentMemoryForgetRule)
                .where(AgentMemoryForgetRule.memory_id == row.id)
                .with_for_update()
            )
            if rule:
                rule.active = 0
                rule.restored_at = utcnow()
            append_revision(self.session, row, "restore", source_values(sources))
            invalidate_memory_runs(self.session, row)
            self.session.flush()
            return memory_read(self.session, row)

    def revisions(self, identifier):
        with self._transaction(read_only=True):
            row = AgentMemoryDAO(self.session).item(parse_identifier(identifier), self._owner())
            if row is None:
                raise NotFound("记忆不存在")
            return [
                {
                    "content_version": str(revision.content_version),
                    "change": revision.change,
                    "snapshot": revision.snapshot,
                    "created_at": revision.created_at,
                }
                for revision in self.session.scalars(
                    select(AgentMemoryRevision)
                    .where(AgentMemoryRevision.memory_id == row.id)
                    .order_by(AgentMemoryRevision.content_version.desc())
                    .limit(50)
                ).all()
            ]
