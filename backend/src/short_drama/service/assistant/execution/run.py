"""Authenticated messages, reviews and cancellation; execution belongs to workers."""

from copy import deepcopy

from sqlalchemy import func, select

from short_drama.agent.core.events import (
    budget_limits,
    initial_usage,
)
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.assistant.execution.run import AgentRunDAO
from short_drama.domain.assistant.conversations.models import (
    AgentConversation,
    AgentEvent,
    AgentMessage,
)
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.identity.accounts.user import User, UserSession
from short_drama.schemas.assistant.operations.runtime import (
    ConversationRuntimeState,
    EventRead,
    MessageRead,
    RunRead,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.assistant.conversations.conversation import AgentConversationService
from short_drama.service.base import Page, utcnow


def read_runs(session, rows):
    """Batch supplemental public metadata without loading execution checkpoints."""
    if not rows:
        return []
    dao = AgentRunDAO(session)
    queued = [row for row in rows if row.status == "queued"]
    queue = dao.queue({row.conversation_id for row in queued}) if queued else []
    return [
        read_run(
            session,
            row,
            queue_position=sum(
                item.conversation_id == row.conversation_id
                and item.id != row.id
                and (item.status != "queued" or item.id < row.id)
                for item in queue
            )
            if row.status == "queued"
            else 0,
        )
        for row in rows
    ]


def read_run(session, run, *, queue_position=None):
    if queue_position is None:
        queue_position = 0
        if run.status == "queued":
            queue_position = (
                session.scalar(
                    select(func.count(AgentRun.id)).where(
                        AgentRun.conversation_id == run.conversation_id,
                        AgentRun.id != run.id,
                        AgentRun.status.in_(("running", "waiting_generation"))
                        | ((AgentRun.status == "queued") & (AgentRun.id < run.id)),
                    )
                )
                or 0
            )
    return RunRead(
        id=run.id,
        task_id=getattr(run, "task_id", None),
        conversation_id=run.conversation_id,
        status=run.status,
        phase=run.phase,
        row_version=run.row_version,
        mode=(run.checkpoint.get("authorization") or {}).get("mode", "discuss"),
        model_config_id=run.model_config_id,
        model_name=run.config_snapshot.get("name", "文本模型"),
        error={"code": run.error.get("code", "agent_failed")} if run.error else None,
        usage={key: (run.usage or {}).get(key, 0) for key in initial_usage()},
        budget={key: (run.budget or {}).get(key, 0) for key in budget_limits()},
        review=None,
        awaiting_artifact_ids=run.checkpoint.get("awaiting_artifacts", []),
        created_at=run.created_at,
        updated_at=run.updated_at,
        finished_at=run.finished_at,
        queue_position=queue_position,
        waiting_reason="等待前序任务" if queue_position else None,
    )


class AgentRunService(AgentConversationService):
    def __init__(self, session, settings, storage=None):
        super().__init__(session, settings)
        self._storage = storage

    @property
    def storage(self):
        from short_drama.service.media.files.storage import StorageService
        from short_drama.storage.minio import MinioStorage

        if not isinstance(self._storage, StorageService):
            self._storage = StorageService(
                self._storage or MinioStorage(self.settings), self.settings
            )
        return self._storage

    def _message_read(self, row):
        from short_drama.domain import MediaFile

        references = deepcopy(row.references)
        for reference in references:
            if reference.get("type") == "attachment" and reference.get("media_id"):
                media = self.session.get(MediaFile, parse_identifier(reference["media_id"]))
                reference["url"] = (
                    self.storage.download_url(media.storage_locator) if media else None
                )
        return MessageRead(
            id=row.id,
            task_id=row.task_id,
            seq=row.seq,
            item_seq=row.item_seq,
            row_version=row.row_version,
            role=row.role,
            kind=row.kind,
            content=row.content,
            references=references,
            artifacts=row.artifacts,
            created_at=row.created_at,
        )

    def _run(self, identifier, *, lock=False):
        identifier = parse_identifier(identifier)
        parent = self.session.scalar(
            select(AgentRun.conversation_id).where(AgentRun.id == identifier)
        )
        if parent is None:
            raise NotFound("Run does not exist")
        conversation = self._conversation(parent, lock=lock)
        stmt = select(AgentRun).where(
            AgentRun.id == identifier, AgentRun.conversation_id == conversation.id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(stmt)
        if row is None:
            raise NotFound("Run does not exist")
        return conversation, row

    def list_messages(self, identifier, offset=0, limit=50):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
            query = select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
            rows = self.session.scalars(
                query.order_by(AgentMessage.seq.desc()).offset(offset).limit(limit)
            ).all()
            total = self.session.scalar(
                select(func.count(AgentMessage.id)).where(
                    AgentMessage.conversation_id == conversation.id
                )
            )
            return Page(
                items=[self._message_read(row) for row in reversed(rows)],
                total=total,
                offset=offset,
                limit=limit,
            )

    def get_run(self, identifier):
        with self._transaction(read_only=True):
            _, run = self._run(identifier)
            return read_run(self.session, run)

    def runtime_state(self, identifier):
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
            rows = sorted(
                AgentRunDAO(self.session).projections(conversation.id, active=True),
                key=lambda row: row.id,
            )
            running = next(
                (row for row in rows if row.status in {"running", "waiting_generation"}), None
            )
            active = running or next(
                (row for row in reversed(rows) if row.status == "waiting_review"), None
            )
            queued = [row for row in rows if row.status == "queued"]
            public = {row.id: row for row in read_runs(self.session, rows)}
            snapshot_cursor = conversation.next_event_seq - 1
            resume_cursor = snapshot_cursor
            if rows:
                first_event = self.session.scalar(
                    select(func.min(AgentEvent.seq)).where(
                        AgentEvent.conversation_id == conversation.id,
                        AgentEvent.run_id.in_([row.id for row in rows]),
                        AgentEvent.seq <= snapshot_cursor,
                    )
                )
                if first_event is not None:
                    resume_cursor = first_event - 1
            return ConversationRuntimeState(
                conversation_id=conversation.id,
                cursor=snapshot_cursor,
                resume_cursor=resume_cursor,
                active_run=public[active.id] if active else None,
                queued_runs=[public[row.id] for row in queued],
            )

    def events(self, identifier, cursor=0, *, verify_session=False):
        with self._transaction():
            actor = self._actor()
            if verify_session:
                user = self.session.get(User, actor.user_id)
                login = self.session.get(UserSession, actor.session_id)
                if (
                    user is None
                    or user.status != "active"
                    or not user.email_verified_at
                    or login is None
                    or login.user_id != actor.user_id
                    or login.revoked_at
                    or login.expires_at <= utcnow()
                ):
                    raise WorkflowError("authentication_required", "Please sign in", 401)
            conversation = self._conversation(identifier)
            rows = self.session.scalars(
                select(AgentEvent)
                .where(AgentEvent.conversation_id == conversation.id, AgentEvent.seq > cursor)
                .order_by(AgentEvent.seq)
                .limit(100)
            ).all()
            if conversation.scope_version == 3:
                ceiling = (
                    self.session.scalar(
                        select(AgentConversation.next_event_seq).where(
                            AgentConversation.id == conversation.id
                        )
                    )
                    - 1
                )
                if (
                    cursor > ceiling
                    or (not rows and cursor < ceiling)
                    or any(row.seq != cursor + index + 1 for index, row in enumerate(rows))
                ):
                    raise WorkflowError(
                        "agent_event_resync_required", "事件游标已失效，请重新读取会话快照", 409
                    )
            return [EventRead.model_validate(row) for row in rows]
