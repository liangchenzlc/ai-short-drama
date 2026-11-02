"""DeepSeek request gaps around the official LangChain integration."""

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_deepseek import ChatDeepSeek


def with_reasoning_content(
    messages: list[BaseMessage], serialized: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = [dict(message) for message in serialized]
    for original, message in zip(messages, result, strict=True):
        if isinstance(original, AIMessage) and "reasoning_content" in original.additional_kwargs:
            message["reasoning_content"] = original.additional_kwargs["reasoning_content"]
    return result


class DeepSeekChatModel(ChatDeepSeek):
    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        choice = payload.get("tool_choice")
        if choice and choice not in ("auto", "none"):
            extra_body = payload.get("extra_body") or {}
            payload["extra_body"] = {
                **extra_body,
                "thinking": {**(extra_body.get("thinking") or {}), "type": "disabled"},
            }
        payload["messages"] = with_reasoning_content(
            self._convert_input(input_).to_messages(), payload["messages"]
        )
        return payload
