from sqlalchemy import func, select

from short_drama.agent.task.controls import apply_controls_locked
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.assistant.conversations.queries import latest_summaries
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.db.scope.resource_scope import require_project
from short_drama.domain.assistant.conversations.models import (
    AgentConversation,
)
from short_drama.domain.assistant.memory.memory import AgentMemoryItem
from short_drama.domain.assistant.tasks.task import (
    AgentTask,
    AgentTaskControl,
)
from short_drama.schemas.assistant.tasks.task import (
    GlobalConversationCreate,
    GlobalConversationDelete,
    GlobalConversationPatch,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.assistant.context.attachment import attachment_key
from short_drama.service.assistant.memory.memory_store import (
    deletion_memory_query,
    deletion_memory_read,
    lock_owner_conversations,
    prepare_deletion_memory_locked,
)
from short_drama.service.assistant.tasks.base import TaskServiceBase
from short_drama.service.base import Page, utcnow
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


class ConversationOperations(TaskServiceBase):
    def create_global_conversation(self, payload, idempotency_key):
        values = self._payload(GlobalConversationCreate, payload)
        key, request_hash = attachment_key(idempotency_key), digest(values)
        actor = self._actor()
        with self._transaction():
            if values.get("project_id") is not None:
                require_project(self.session, values["project_id"], lock=True)
            row = self.session.scalar(
                select(AgentConversation)
                .where(
                    AgentConversation.owner_user_id == actor.user_id,
                    AgentConversation.create_key == key,
                )
                .with_for_update()
            )
            if row is not None:
                if row.scope_version != 3 or row.create_hash != request_hash:
                    raise Conflict("此请求身份已经用于其他对话")
                return self._read_conversation(row)
            now = utcnow()
            row = AgentConversation(
                id=next_id(),
                owner_user_id=actor.user_id,
                project_id=values.get("project_id"),
                stage=None,
                subject_type=None,
                subject_id=None,
                task_type=None,
                scope_version=3,
                title=values.get("title", "新对话"),
                status="active",
                row_version=1,
                next_message_seq=1,
                next_event_seq=1,
                fixed_requirements={},
                create_key=key,
                create_hash=request_hash,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            self.session.flush()
            return self._read_conversation(row)

    def list_global_conversations(self, offset=0, limit=20, *, search=None, project_id=None):
        actor = self._actor()
        self.dao.validate_pagination(offset, limit)
        conditions = [
            AgentConversation.owner_user_id == actor.user_id,
            AgentConversation.scope_version == 3,
            AgentConversation.status.in_(("active", "deleting")),
        ]
        if search and search.strip():
            conditions.append(AgentConversation.title.contains(search.strip(), autoescape=True))
        with self._transaction(read_only=True):
            if project_id is not None:
                project_id = parse_identifier(project_id)
                require_project(self.session, project_id)
                conditions.append(AgentConversation.project_id == project_id)
            rows = self.session.scalars(
                select(AgentConversation)
                .where(*conditions)
                .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            statuses, previews = latest_summaries(self.session, [row.id for row in rows])
            return Page(
                items=[
                    self._read_conversation(
                        row, last_status=statuses.get(row.id), last_message=previews.get(row.id)
                    )
                    for row in rows
                ],
                total=self.session.scalar(
                    select(func.count()).select_from(AgentConversation).where(*conditions)
                ),
                offset=offset,
                limit=limit,
            )

    def rename_global_conversation(self, identifier, payload):
        values = self._payload(GlobalConversationPatch, payload)
        with self._transaction():
            row = self._writable(identifier)
            if row.row_version != values["row_version"]:
                raise Conflict("对话已变化，请重新读取后修改标题")
            row.title, row.updated_at = values["title"], utcnow()
            row.row_version += 1
            self.session.flush()
            return self._read_conversation(row)

    def deletion_memory_preview(self, identifier, offset=0, limit=30):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(identifier)
            if conversation.scope_version != 3 or conversation.status != "active":
                raise Conflict("此对话已停止使用，请核对删除状态")
            query = deletion_memory_query(conversation)
            rows = self.session.scalars(
                query.order_by(AgentMemoryItem.id).offset(offset).limit(limit)
            ).all()
            return {
                "conversation_id": str(conversation.id),
                "row_version": str(conversation.row_version),
                "items": [deletion_memory_read(self.session, row, conversation) for row in rows],
                "total": self.session.scalar(select(func.count()).select_from(query.subquery())),
                "offset": offset,
                "limit": limit,
            }

    def delete_global_conversation(self, identifier, payload, idempotency_key):
        from short_drama.agent.core.events import append_event
        from short_drama.agent.session.lifecycle import finalize_deletion_locked

        values = self._payload(GlobalConversationDelete, payload)
        key, request_hash = attachment_key(idempotency_key), digest(values)
        with self._transaction():
            actor = self._actor()
            lock_owner_conversations(self.session, actor.user_id, include_deleting=True)
            identifier = parse_identifier(identifier)
            conversation = self.session.scalar(
                select(AgentConversation)
                .where(
                    AgentConversation.id == identifier,
                    AgentConversation.owner_user_id == actor.user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True, assistant_deletion_receipt_id=identifier)
            )
            if conversation is None:
                raise NotFound("对话不存在")
            if conversation.scope_version != 3:
                raise WorkflowError("assistant_legacy_readonly", "历史对话仅供查看", 409)
            deletion = conversation.fixed_requirements.get("deletion")
            if deletion is not None:
                if deletion["key"] != key or deletion["hash"] != request_hash:
                    raise Conflict("此对话已经申请删除，请核对当前状态")
                return self._read_conversation(conversation, last_status=None, last_message="")
            if conversation.row_version != values["row_version"]:
                raise Conflict("对话已变化，请核对当前任务后删除")
            prepare_deletion_memory_locked(
                self.session, conversation, values.get("memory_selections", [])
            )
            conversation.status = "deleting"
            conversation.row_version += 1
            conversation.fixed_requirements = {
                "deletion": {
                    "key": key,
                    "hash": request_hash,
                    "clear_derived_memory": values.get("clear_derived_memory", False),
                    "memory_selections": values.get("memory_selections", []),
                }
            }
            append_event(
                self.session,
                conversation,
                "conversation.deleting",
                {
                    "id": str(conversation.id),
                    "state_version": str(conversation.row_version),
                },
            )
            dao = AgentTaskDAO(self.session)
            tasks = self.session.scalars(
                select(AgentTask)
                .where(
                    AgentTask.conversation_id == conversation.id,
                    AgentTask.status.in_(("queued", "running", "waiting_media")),
                )
                .order_by(AgentTask.id)
                .with_for_update()
            ).all()
            for task in tasks:
                run = dao.latest_run(task.id)
                active = dao.active_operation(task.id)
                task.control_seq += 1
                task.control_state = "delete_pending"
                task.row_version += 1
                self.session.add(
                    AgentTaskControl(
                        id=next_id(),
                        task_id=task.id,
                        seq=task.control_seq,
                        kind="delete",
                        request_key=key,
                        request_hash=request_hash,
                        requested_after_operation_id=active.id if active else None,
                        created_at=utcnow(),
                    )
                )
                self.session.flush()
                apply_controls_locked(self.session, conversation, task, run)
            self.session.flush()
            finalize_deletion_locked(self.session, conversation)
            self.session.flush()
            return self._read_conversation(conversation)
