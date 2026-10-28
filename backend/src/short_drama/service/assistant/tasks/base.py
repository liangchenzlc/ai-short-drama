from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.assistant.tasks.task import AgentTaskDAO
from short_drama.domain.assistant.tasks.task import (
    AgentTask,
)
from short_drama.schemas.base import parse_identifier
from short_drama.service.assistant.execution.run import AgentRunService


class TaskServiceBase(AgentRunService):
    def validate_attachment_conversation(self, identifier):
        with self._transaction(read_only=True):
            self._writable(identifier)

    def _writable(self, identifier):
        conversation = self._conversation(identifier, lock=True)
        if conversation.scope_version != 3:
            raise WorkflowError(
                "assistant_legacy_readonly", "历史对话仅供查看，请在项目中新建对话", 409
            )
        if conversation.status != "active":
            raise WorkflowError(
                "assistant_conversation_deleting", "对话正在删除，不能继续执行", 409
            )
        return conversation

    def _task(self, identifier):
        identifier = parse_identifier(identifier)
        parent = self.session.scalar(
            select(AgentTask.conversation_id).where(AgentTask.id == identifier)
        )
        if parent is None:
            raise NotFound("任务不存在")
        conversation = self._writable(parent)
        task = AgentTaskDAO(self.session).task(
            identifier, conversation_id=conversation.id, lock=True
        )
        return conversation, task
