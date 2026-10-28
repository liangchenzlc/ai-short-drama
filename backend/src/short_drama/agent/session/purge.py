"""Durable deletion cleanup; business outputs and independently held media survive."""

import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from short_drama.domain import MediaFile
from short_drama.domain.agent import (
    AgentConversation,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    AgentTurn,
)
from short_drama.domain.agent_context import AgentAttachment
from short_drama.domain.agent_graph import AgentGraphCheckpoint, AgentGraphWrite
from short_drama.domain.agent_history import AgentDiscussionRevision
from short_drama.domain.agent_media import AgentMediaRequest
from short_drama.domain.agent_memory import (
    AgentConversationSummary,
    AgentMemoryItem,
    AgentMemoryJob,
    AgentMemoryRevision,
)
from short_drama.domain.agent_task import AgentOperation, AgentTask, AgentTaskInput
from short_drama.domain.agent_task_requirement import AgentTaskRequirement
from short_drama.domain.agent_undo import AgentBatchUndo
from short_drama.domain.base import Base
from short_drama.service.base import utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id

logger = logging.getLogger(__name__)
DELETED = "[已删除]"


def enqueue_purge_locked(session, conversation):
    key = digest({"purge_conversation": str(conversation.id)})
    prior = session.scalar(
        select(AgentMemoryJob.id).where(
            AgentMemoryJob.owner_user_id == conversation.owner_user_id,
            AgentMemoryJob.request_key == key,
        )
    )
    if prior is not None:
        return
    session.add(
        AgentMemoryJob(
            id=next_id(),
            owner_user_id=conversation.owner_user_id,
            conversation_id=conversation.id,
            kind="purge",
            request_key=key,
            from_seq=1,
            to_seq=max(1, conversation.next_message_seq - 1),
            source_hash=digest(conversation.fixed_requirements.get("deletion", {})),
            source_snapshot={},
            status="pending",
            message_status="idle",
            next_run_at=utcnow(),
            created_at=utcnow(),
        )
    )


def _rows(session, model, condition):
    return session.scalars(
        select(model).where(condition).order_by(model.id).with_for_update()
    ).all()


def _error_code(value):
    return {"code": value["code"]} if value and isinstance(value.get("code"), str) else None


def _media_referenced(session, identifier):
    # Use all registered FK contracts, including histories, other owners and task receipts.
    connection = session.connection()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if any(foreign.column is MediaFile.__table__.c.id for foreign in column.foreign_keys):
                if (
                    connection.scalar(select(column).where(column == identifier).limit(1))
                    is not None
                ):
                    return True
    return False


def _scrub_locked(session, conversation):
    tasks = _rows(session, AgentTask, AgentTask.conversation_id == conversation.id)
    runs = _rows(session, AgentRun, AgentRun.conversation_id == conversation.id)
    if any(row.status in {"queued", "running", "waiting_media"} for row in tasks) or any(
        row.status in {"queued", "running", "waiting_generation", "waiting_review"} for row in runs
    ):
        raise ValueError("Deletion cleanup cannot precede terminal execution")
    task_ids, run_ids = [row.id for row in tasks], [row.id for row in runs]
    for row in tasks:
        row.goal, row.context_snapshot, row.selected_skills = DELETED, None, []
        row.effective_state, row.memory_project_ids = {}, []
        row.error = _error_code(row.error)
    for row in _rows(session, AgentTaskInput, AgentTaskInput.task_id.in_(task_ids)):
        row.payload = {}
    for row in _rows(session, AgentTaskRequirement, AgentTaskRequirement.task_id.in_(task_ids)):
        # Descriptions and spans quote the user's own words; clear them with the task.
        row.description = DELETED
        row.quote_start, row.quote_end = 0, 0
    for row in runs:
        row.checkpoint, row.config_snapshot, row.error = {}, {}, _error_code(row.error)
    derived_locators = set()
    for row in _rows(session, AgentTurn, AgentTurn.run_id.in_(run_ids)):
        for envelope in row.request_messages or []:
            for intent in envelope.get("kwargs", {}).get("hydration_intents", []):
                reference = intent.get("resource", {})
                locator = reference.get("uri", "")
                # Only the Resolver's per-turn immutable derivatives are private
                # cleanup candidates. User/business originals remain protected.
                if (
                    reference.get("source") in {"hydrated_image", "hydrated_audio"}
                    and f"/agent-harness/turn/{row.id}/" in locator
                ):
                    derived_locators.add(locator)
        row.request_messages, row.response, row.error = [], None, _error_code(row.error)
    for model in (AgentGraphCheckpoint, AgentGraphWrite):
        for row in _rows(session, model, model.run_id.in_(run_ids)):
            session.delete(row)
    for row in _rows(session, AgentToolCall, AgentToolCall.run_id.in_(run_ids)):
        row.arguments, row.result = {}, None
        row.review_payload, row.error = None, _error_code(row.error)
    for row in _rows(session, AgentOperation, AgentOperation.task_id.in_(task_ids)):
        if row.status in {"running", "waiting_media", "reconciling"}:
            raise ValueError("Deletion cleanup cannot precede operation settlement")
        row.arguments, row.result, row.changes, row.error = {}, None, [], _error_code(row.error)
    for row in _rows(session, AgentMediaRequest, AgentMediaRequest.task_id.in_(task_ids)):
        row.frozen_request, row.frozen_target, row.result = {}, {}, None
        row.error = _error_code(row.error)
    for row in _rows(session, AgentBatchUndo, AgentBatchUndo.conversation_id == conversation.id):
        row.selected_keys, row.changes, row.result_refs = [], [], []
    for row in _rows(session, AgentMessage, AgentMessage.conversation_id == conversation.id):
        row.content, row.references, row.artifacts = DELETED, [], []
    for row in _rows(
        session, AgentDiscussionRevision, AgentDiscussionRevision.conversation_id == conversation.id
    ):
        row.source_snapshot, row.reply_snapshot = {}, {}
    for row in _rows(session, AgentEvent, AgentEvent.conversation_id == conversation.id):
        row.payload = (
            {"id": str(conversation.id)} if row.event_type == "conversation.deleted" else {}
        )
    for row in _rows(
        session,
        AgentConversationSummary,
        AgentConversationSummary.conversation_id == conversation.id,
    ):
        row.content, row.status = {}, "invalidated"
    for row in _rows(session, AgentMemoryJob, AgentMemoryJob.conversation_id == conversation.id):
        if row.kind != "purge":
            row.source_snapshot, row.raw_result, row.error = {}, None, _error_code(row.error)
    selected = {
        int(item["id"]): int(item["content_version"]) + 1
        for item in conversation.fixed_requirements.get("deletion", {}).get("memory_selections", [])
    }
    memories = _rows(
        session,
        AgentMemoryItem,
        or_(
            AgentMemoryItem.conversation_id == conversation.id,
            AgentMemoryItem.id.in_(selected),
        ),
    )
    for row in memories:
        if row.conversation_id != conversation.id and (
            row.state != "forgotten" or row.content_version != selected[row.id]
        ):
            continue
        row.content, row.title, row.search_text, row.aliases, row.tags = (
            DELETED,
            DELETED,
            "",
            [],
            [],
        )
        for revision in _rows(
            session, AgentMemoryRevision, AgentMemoryRevision.memory_id == row.id
        ):
            revision.snapshot = {
                **revision.snapshot,
                "content": DELETED,
                "title": DELETED,
                "aliases": [],
                "tags": [],
            }
    candidates = set()
    for attachment in _rows(
        session, AgentAttachment, AgentAttachment.conversation_id == conversation.id
    ):
        if attachment.media_id and not attachment.input_metadata.get("source_type"):
            candidates.add(attachment.media_id)
        session.delete(attachment)
    session.flush()
    media_deletions = sorted(derived_locators)
    for identifier in sorted(candidates):
        media = session.scalar(
            select(MediaFile).where(MediaFile.id == identifier).with_for_update()
        )
        if (
            media is None
            or media.scope_user_id != conversation.owner_user_id
            or media.project_id is not None
            or _media_referenced(session, identifier)
        ):
            continue
        shared = session.scalar(
            select(MediaFile.id)
            .where(
                MediaFile.id != identifier,
                MediaFile.storage_locator == media.storage_locator,
            )
            .limit(1)
        )
        if shared is None:
            media_deletions.append(media.storage_locator)
        session.delete(media)
    conversation.title = DELETED
    return media_deletions


def purge_one(factory, storage):
    """Run without model credentials or RabbitMQ, also when Agent generation is disabled."""
    now = utcnow()
    with factory.begin() as session:
        identifiers = session.scalars(
            select(AgentMemoryJob.id)
            .where(
                AgentMemoryJob.kind == "purge",
                AgentMemoryJob.status.in_(("pending", "running")),
                AgentMemoryJob.next_run_at <= now,
                or_(AgentMemoryJob.lease_until.is_(None), AgentMemoryJob.lease_until <= now),
            )
            .order_by(AgentMemoryJob.id)
            .limit(20)
        ).all()
        job = None
        for identifier in identifiers:
            candidate = session.scalar(
                select(AgentMemoryJob)
                .where(
                    AgentMemoryJob.id == identifier,
                )
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if (
                candidate
                and candidate.status in {"pending", "running"}
                and (candidate.lease_until is None or candidate.lease_until <= now)
            ):
                job = candidate
                break
        if job is None:
            return False
        conversation = session.scalar(
            select(AgentConversation)
            .where(
                AgentConversation.id == job.conversation_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if conversation is None or conversation.status != "deleted":
            job.next_run_at = now + timedelta(seconds=10)
            return True
        snapshot = dict(job.source_snapshot)
        if not snapshot.get("scrubbed"):
            snapshot = {"scrubbed": True, "media_locators": _scrub_locked(session, conversation)}
        job.source_snapshot, job.status = snapshot, "running"
        job.attempts += 1
        job.lease_token, job.lease_until = uuid4().hex, now + timedelta(minutes=5)
        job_id, token, locators = job.id, job.lease_token, snapshot["media_locators"]
    failure = False
    try:
        for locator in locators:
            storage.delete(locator)
    except Exception as error:
        failure = True
        logger.warning(
            "Agent conversation media cleanup failed: job=%s error=%s", job_id, type(error).__name__
        )
    with factory.begin() as session:
        job = session.scalar(
            select(AgentMemoryJob)
            .where(
                AgentMemoryJob.id == job_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None or job.lease_token != token:
            return True
        job.lease_token = job.lease_until = None
        if failure:
            job.status, job.error = "pending", {"code": "agent_purge_storage_unavailable"}
            job.next_run_at = utcnow() + timedelta(seconds=min(3600, 2 ** min(job.attempts, 12)))
        else:
            job.status, job.error, job.finished_at = "succeeded", None, utcnow()
            job.source_snapshot = {"scrubbed": True}
    return True
