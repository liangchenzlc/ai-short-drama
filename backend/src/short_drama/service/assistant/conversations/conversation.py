"""Private creation conversations, with project-first locks and optimistic edits."""

from sqlalchemy import func, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.db.scope.resource_scope import require_project
from short_drama.domain.assistant.conversations.models import AgentConversation, AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.schemas.assistant.conversations.engine import (
    ConversationRead,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.base import BaseService

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

    def _conversation(self, identifier, *, lock=False):
        actor = self._actor()
        identifier = parse_identifier(identifier)
        # 所有写锁先从项目开始，再锁会话，避免跨入口以相反顺序取锁。
        parent = self.session.execute(
            select(AgentConversation.project_id, AgentConversation.scope_version).where(
                AgentConversation.id == identifier, AgentConversation.owner_user_id == actor.user_id
            )
        ).first()
        if parent is None:
            raise NotFound("Conversation does not exist")
        if parent.project_id is not None:
            require_project(self.session, parent.project_id, lock=lock)
        statement = select(AgentConversation).where(
            AgentConversation.id == identifier, AgentConversation.owner_user_id == actor.user_id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(statement)
        # 先核对拥有者与项目权限，再拒绝已退役会话，避免旧身份进入执行链。
        if row is None or row.status == "deleted" or row.scope_version != 3:
            raise NotFound("Conversation does not exist")
        return row

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
            title=row.title,
            row_version=row.row_version,
            archived=row.status == "archived",
            status=row.status,
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

    def get_conversation(self, identifier):
        with self._transaction(read_only=True):
            return self._read_conversation(self._conversation(identifier))
