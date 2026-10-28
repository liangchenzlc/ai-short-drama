from short_drama.service.assistant.tasks.controls import TaskControlOperations
from short_drama.service.assistant.tasks.conversations import ConversationOperations
from short_drama.service.assistant.tasks.inputs import TaskInputOperations
from short_drama.service.assistant.tasks.messages import TaskMessageOperations
from short_drama.service.assistant.tasks.queries import TaskQueryOperations


class AssistantTaskService(
    ConversationOperations,
    TaskInputOperations,
    TaskMessageOperations,
    TaskControlOperations,
    TaskQueryOperations,
):
    """组合独立职责；共享同一会话，事务仍由具体操作完整持有。"""
