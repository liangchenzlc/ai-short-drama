"""不连接供应商的确定性聊天模型，供助手运行时契约测试使用。"""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr


class ScriptedModel(BaseChatModel):
    replies: list[AIMessage] = Field(exclude=True)
    _position: int = PrivateAttr(default=0)
    _requests: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    @property
    def requests(self) -> list[list[BaseMessage]]:
        return self._requests

    @property
    def _llm_type(self) -> str:
        return "assistant-test-script"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._requests.append(list(messages))
        reply = self.replies[self._position]
        self._position += 1
        return ChatResult(generations=[ChatGeneration(message=reply)])
