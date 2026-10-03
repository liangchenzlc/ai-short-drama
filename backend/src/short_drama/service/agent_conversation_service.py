"""Private episode conversations, with project-first locks and optimistic edits."""

import hashlib
import json

from sqlalchemy import func, select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.db.access import require_project
from short_drama.domain import Episode
from short_drama.domain.agent import AgentConversation, AgentRun
from short_drama.schemas.agent import ConversationCreate, ConversationPatch, ConversationRead
from short_drama.schemas.base import parse_identifier
from short_drama.service.base import BaseService, Page, utcnow
from short_drama.utils.snowflake import next_id

ACTIVE_RUN_STATES = ("queued", "running", "waiting_generation", "waiting_review")
_UNSET = object()


class AgentConversationService(BaseService):
    model = AgentConversation

    def __init__(self, session, settings):
        super().__init__(session)
        self.settings = settings

    def _actor(self):
        actor = require_actor(self.session)
        if not self.settings.agent_enabled:
            raise WorkflowError("agent_disabled", "Agent 模式尚未启用", 503)
        return actor

    def _episode(self, project_id, episode_id, *, lock=True):
        require_project(self.session, project_id, lock=lock)
        row = self.session.scalar(
            select(Episode).where(Episode.id == episode_id, Episode.project_id == project_id)
        )
        if row is None:
            raise NotFound("Episode does not exist")
        return row

    def _conversation(self, identifier, *, lock=False):
        actor = self._actor()
        identifier = parse_identifier(identifier)
        # A scoped lookup finds the parent; all locks start at Project.
        parent = self.session.scalar(
            select(AgentConversation.project_id).where(
                AgentConversation.id == identifier, AgentConversation.owner_user_id == actor.user_id
            )
        )
        if parent is None:
            raise NotFound("Conversation does not exist")
        require_project(self.session, parent, lock=lock)
        statement = select(AgentConversation).where(
            AgentConversation.id == identifier, AgentConversation.owner_user_id == actor.user_id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(statement)
        if row is None:
            raise NotFound("Conversation does not exist")
        return row

    def _read_conversation(self, row, *, last_status=_UNSET):
        if last_status is _UNSET:
            last_status = self.session.scalar(
                select(AgentRun.status)
                .where(AgentRun.conversation_id == row.id)
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .limit(1)
            )
        return ConversationRead(
            id=row.id,
            project_id=row.project_id,
            episode_id=row.episode_id,
            title=row.title,
            row_version=row.row_version,
            archived=row.status == "archived",
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_run_status=last_status,
        )

    def create_conversation(self, payload, idempotency_key=None):
        actor = self._actor()
        values = self._payload(ConversationCreate, payload)
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 64
            or not all(
                char.isascii() and (char.isalnum() or char in "_-:") for char in idempotency_key
            )
        ):
            raise WorkflowError("invalid_idempotency_key", "Invalid idempotency key", 422)
        key = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
        digest = hashlib.sha256(
            json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        with self._transaction():
            self._episode(values["project_id"], values["episode_id"])
            if key:
                existing = self.session.scalar(
                    select(AgentConversation).where(
                        AgentConversation.owner_user_id == actor.user_id,
                        AgentConversation.create_key == key,
                    )
                )
                if existing:
                    if existing.create_hash != digest:
                        raise Conflict("Idempotency key was used for a different conversation")
                    return self._read_conversation(existing)
            now = utcnow()
            row = AgentConversation(
                id=next_id(),
                owner_user_id=actor.user_id,
                **values,
                status="active",
                row_version=1,
                next_message_seq=1,
                next_event_seq=1,
                create_key=key,
                create_hash=digest if key else None,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            self.session.flush()
            return self._read_conversation(row)

    def list_conversations(
        self, project_id, episode_id, offset=0, limit=20, include_archived=False
    ):
        actor = self._actor()
        self.dao.validate_pagination(offset, limit)
        project_id, episode_id = parse_identifier(project_id), parse_identifier(episode_id)
        with self._transaction(read_only=True):
            self._episode(project_id, episode_id, lock=False)
            conditions = [
                AgentConversation.owner_user_id == actor.user_id,
                AgentConversation.project_id == project_id,
                AgentConversation.episode_id == episode_id,
            ]
            if not include_archived:
                conditions.append(AgentConversation.status == "active")
            rows = self.session.scalars(
                select(AgentConversation)
                .where(*conditions)
                .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            statuses = {}
            if rows:
                latest = (
                    select(
                        AgentRun.conversation_id,
                        AgentRun.status,
                        func.row_number()
                        .over(
                            partition_by=AgentRun.conversation_id,
                            order_by=(AgentRun.created_at.desc(), AgentRun.id.desc()),
                        )
                        .label("rank"),
                    )
                    .where(AgentRun.conversation_id.in_([row.id for row in rows]))
                    .subquery()
                )
                statuses = dict(
                    self.session.execute(
                        select(latest.c.conversation_id, latest.c.status).where(latest.c.rank == 1)
                    ).all()
                )
            return Page(
                items=[
                    self._read_conversation(row, last_status=statuses.get(row.id)) for row in rows
                ],
                total=self.session.scalar(
                    select(func.count(AgentConversation.id)).where(*conditions)
                ),
                offset=offset,
                limit=limit,
            )

    def get_conversation(self, identifier):
        with self._transaction(read_only=True):
            return self._read_conversation(self._conversation(identifier))

    def patch_conversation(self, identifier, payload):
        values = self._payload(ConversationPatch, payload)
        with self._transaction():
            row = self._conversation(identifier, lock=True)
            if row.row_version != values.pop("row_version"):
                raise WorkflowError(
                    "version_conflict",
                    "Conversation changed; reload and retry",
                    details={"current_version": row.row_version},
                )
            if values.get("archived"):
                active = self.session.scalar(
                    select(AgentRun.id)
                    .where(
                        AgentRun.conversation_id == row.id, AgentRun.status.in_(ACTIVE_RUN_STATES)
                    )
                    .limit(1)
                    .with_for_update()
                )
                if active:
                    raise WorkflowError("agent_run_active", "Stop the active task before archiving")
            changes = {}
            if "title" in values and values["title"] != row.title:
                changes["title"] = values["title"]
            if "archived" in values:
                status = "archived" if values["archived"] else "active"
                if status != row.status:
                    changes["status"] = status
            if changes:
                for name, value in changes.items():
                    setattr(row, name, value)
                row.row_version += 1
                row.updated_at = utcnow()
                self.session.flush()
            return self._read_conversation(row)
