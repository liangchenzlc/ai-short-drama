"""Private episode conversations, with project-first locks and optimistic edits."""

import hashlib
import json

from sqlalchemy import and_, func, select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.db.access import require_project
from short_drama.domain import Asset, Episode, EpisodeAsset, ShotScript
from short_drama.domain.agent import AgentConversation, AgentMessage, AgentRun
from short_drama.schemas.agent import (
    ConversationCreate,
    ConversationPatch,
    ConversationRead,
    ConversationScope,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.base import BaseService, Page, utcnow
from short_drama.utils.snowflake import next_id

ACTIVE_RUN_STATES = ("queued", "running", "waiting_generation", "waiting_review")
_UNSET = object()
SCOPE_FIELDS = ("stage", "subject_type", "subject_id", "task_type")


def conversation_scope(conversation):
    """Stable scope metadata; titles and positions are never identity fields."""
    if conversation.scope_version != 1:
        return None
    return {name: getattr(conversation, name) for name in SCOPE_FIELDS}


def validate_conversation_subject(session, conversation, *, lock=False):
    """Validate current membership without altering historical conversation identity."""
    if conversation.scope_version != 1:
        return None
    if conversation.subject_type == "episode":
        statement = select(Episode).where(
            Episode.id == conversation.subject_id,
            Episode.id == conversation.episode_id,
            Episode.project_id == conversation.project_id,
        )
    elif conversation.subject_type == "asset":
        statement = (
            select(Asset)
            .join(EpisodeAsset, EpisodeAsset.asset_id == Asset.id)
            .where(
                Asset.id == conversation.subject_id,
                Asset.project_id == conversation.project_id,
                EpisodeAsset.episode_id == conversation.episode_id,
            )
        )
    else:
        statement = select(ShotScript).where(
            ShotScript.id == conversation.subject_id,
            ShotScript.episode_id == conversation.episode_id,
            ShotScript.deleted_at.is_(None),
        )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    subject = session.scalar(statement)
    if subject is None:
        raise WorkflowError(
            "agent_subject_unavailable", "当前创作对象已删除或脱离本集，请切换对象", 409
        )
    return subject


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

    def _conversation(self, identifier, *, lock=False, expected_scope=None):
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
        self.check_expected_scope(row, expected_scope)
        return row

    def check_expected_scope(self, conversation, expected_scope, *, validate_subject=False):
        """Caller owns the transaction; workspace reads must supply their expected scope."""
        if expected_scope is not None:
            scope = ConversationScope.model_validate(
                expected_scope.model_dump()
                if isinstance(expected_scope, ConversationScope)
                else expected_scope
            )
            if conversation.scope_version != 1 or any(
                getattr(conversation, name) != getattr(scope, name)
                for name in SCOPE_FIELDS
                if name != "task_type" or scope.task_type is not None
            ):
                raise WorkflowError(
                    "agent_scope_mismatch", "此对话不属于当前创作对象，请切换正确的对话", 409
                )
        if validate_subject:
            self.require_scoped_subject(conversation)
        return conversation

    def require_scoped_subject(self, conversation, *, lock=True):
        return validate_conversation_subject(self.session, conversation, lock=lock)

    def _read_conversation(self, row, *, last_status=_UNSET, last_message=_UNSET):
        if last_status is _UNSET:
            last_status = self.session.scalar(
                select(AgentRun.status)
                .where(AgentRun.conversation_id == row.id)
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .limit(1)
            )
        if last_message is _UNSET:
            last_message = self.session.scalar(
                select(func.substring(AgentMessage.content, 1, 200))
                .where(AgentMessage.conversation_id == row.id)
                .order_by(AgentMessage.seq.desc())
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
            last_message_preview=last_message or "",
            stage=row.stage,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            task_type=row.task_type,
            scope_version=row.scope_version,
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
            scope_version = 1 if values.get("stage") is not None else 0
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
                scope_version=scope_version,
                create_key=key,
                create_hash=digest if key else None,
                created_at=now,
                updated_at=now,
            )
            self.require_scoped_subject(row)
            self.session.add(row)
            self.session.flush()
            return self._read_conversation(row)

    def resolve_conversation(self, payload):
        """Project locking serializes even the first resolve with an empty scope history."""
        actor = self._actor()
        values = self._payload(ConversationCreate, payload)
        if values.get("stage") is None:
            raise WorkflowError("agent_scope_required", "进入创作对象需要完整的会话范围", 422)
        with self._transaction():
            self._episode(values["project_id"], values["episode_id"])
            candidate = AgentConversation(
                owner_user_id=actor.user_id,
                **values,
                scope_version=1,
            )
            self.require_scoped_subject(candidate)
            conditions = [
                AgentConversation.owner_user_id == actor.user_id,
                AgentConversation.project_id == values["project_id"],
                AgentConversation.episode_id == values["episode_id"],
                AgentConversation.scope_version == 1,
                AgentConversation.status == "active",
                *(getattr(AgentConversation, name) == values[name] for name in SCOPE_FIELDS),
            ]
            existing = self.session.scalar(
                select(AgentConversation)
                .where(*conditions)
                .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                .limit(1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if existing is not None:
                return self._read_conversation(existing)
            now = utcnow()
            candidate.id = next_id()
            candidate.status, candidate.row_version = "active", 1
            candidate.next_message_seq = candidate.next_event_seq = 1
            candidate.created_at = candidate.updated_at = now
            self.session.add(candidate)
            self.session.flush()
            return self._read_conversation(candidate)

    def list_conversations(
        self,
        project_id,
        episode_id,
        offset=0,
        limit=20,
        include_archived=False,
        *,
        scope=None,
        search=None,
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
            if scope is None:
                # Unclassified records never enter a scoped object's history.
                conditions.append(AgentConversation.scope_version == 0)
            else:
                scope = ConversationScope.model_validate(
                    scope.model_dump() if isinstance(scope, ConversationScope) else scope
                )
                candidate = AgentConversation(
                    project_id=project_id,
                    episode_id=episode_id,
                    scope_version=1,
                    **scope.model_dump(),
                )
                if not include_archived:
                    self.require_scoped_subject(candidate, lock=False)
                conditions.extend(
                    [
                        AgentConversation.scope_version == 1,
                        *(
                            getattr(AgentConversation, name) == getattr(scope, name)
                            for name in SCOPE_FIELDS
                            if name != "task_type" or scope.task_type is not None
                        ),
                    ]
                )
            if search is not None:
                if not isinstance(search, str) or len(search) > 120:
                    raise WorkflowError("invalid_search", "对话搜索最多 120 个字", 422)
                if search.strip():
                    conditions.append(
                        AgentConversation.title.contains(search.strip(), autoescape=True)
                    )
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
            previews = {}
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
                latest_message = (
                    select(
                        AgentMessage.conversation_id,
                        func.substring(AgentMessage.content, 1, 200).label("preview"),
                        func.row_number()
                        .over(
                            partition_by=AgentMessage.conversation_id,
                            order_by=AgentMessage.seq.desc(),
                        )
                        .label("rank"),
                    )
                    .where(AgentMessage.conversation_id.in_([row.id for row in rows]))
                    .subquery()
                )
                projections = self.session.execute(
                    select(AgentConversation.id, latest.c.status, latest_message.c.preview)
                    .outerjoin(
                        latest,
                        and_(latest.c.conversation_id == AgentConversation.id, latest.c.rank == 1),
                    )
                    .outerjoin(
                        latest_message,
                        and_(
                            latest_message.c.conversation_id == AgentConversation.id,
                            latest_message.c.rank == 1,
                        ),
                    )
                    .where(AgentConversation.id.in_([row.id for row in rows]))
                ).all()
                statuses = {identifier: status for identifier, status, _preview in projections}
                previews = {identifier: preview for identifier, _status, preview in projections}
            return Page(
                items=[
                    self._read_conversation(
                        row, last_status=statuses.get(row.id), last_message=previews.get(row.id)
                    )
                    for row in rows
                ],
                total=self.session.scalar(
                    select(func.count(AgentConversation.id)).where(*conditions)
                ),
                offset=offset,
                limit=limit,
            )

    def get_conversation(self, identifier, expected_scope=None):
        with self._transaction(read_only=True):
            return self._read_conversation(
                self._conversation(identifier, expected_scope=expected_scope)
            )

    def require_writable_conversation(self, identifier, expected_scope=None):
        """HTTP compatibility records are read-only; internal legacy service tests remain valid."""
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier, expected_scope=expected_scope)
            if conversation.scope_version != 1:
                raise WorkflowError(
                    "agent_legacy_conversation_readonly",
                    "旧版未分类对话仅供查看，请进入当前创作对象新建对话",
                    409,
                )
            self.require_scoped_subject(conversation, lock=False)
            return self._read_conversation(conversation)

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
