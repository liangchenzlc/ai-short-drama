"""Process-local question dependencies and public framework persistence facts."""

from langchain_core.messages import ToolMessage
from langgraph.types import Command


class RuntimeQuestions:
    def __init__(self, context):
        self.context = context

    def _call(self, method, *args, **kwargs):
        from short_drama.service.assistant.operations.questions import QuestionService

        with self.context.factory() as session:
            return getattr(QuestionService(session, self.context.settings), method)(*args, **kwargs)

    def register(self, *args, **kwargs):
        return self._call("register", *args, **kwargs)

    def read_answer(self, *args, **kwargs):
        return self._call("read_answer", *args, **kwargs)


def persistence_facts(writes):
    interrupts, completed = [], set()

    def messages(value):
        if isinstance(value, ToolMessage):
            if operation_id := value.additional_kwargs.get("wait_operation_id"):
                completed.add(operation_id)
        elif isinstance(value, Command):
            messages(value.update)
        elif isinstance(value, dict):
            for child in value.values():
                messages(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                messages(child)

    for channel, value in writes:
        if channel == "__interrupt__":
            for item in value:
                if isinstance(item.value, dict) and item.value.get("wait_operation_id"):
                    interrupts.append(
                        {
                            "operation_id": item.value["wait_operation_id"],
                            "kind": item.value["kind"],
                            "interrupt_id": item.id,
                        }
                    )
        else:
            messages(value)
    return interrupts, completed
