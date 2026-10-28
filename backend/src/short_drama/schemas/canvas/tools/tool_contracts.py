"""仍可达编辑器工具的闭合合同；仅传稳定引用，不接收客户端供应商协议。"""

import json
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
from typing import Annotated, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from short_drama.schemas.base import InputModel, parse_identifier
from short_drama.schemas.canvas.generation.generation import CanvasGenerationProtocol
from short_drama.schemas.canvas.generation.text.text_parameters import CanvasTextReference

TOOL_COMMIT = "4ca2a65a7780a8dfcaaa86c33679f84fb04e055c"
TOOL_REQUEST_BYTES = 512 * 1024
TOOL_REPLY_BYTES = 512 * 1024


@lru_cache(maxsize=1)
def _builtin_tools() -> dict[str, dict]:
    # 合同属于应用资源，不能依赖 Schema 文件所在目录的层数。
    path = files("short_drama.ai").joinpath("data/beeftv_tool_contracts.json")
    try:
        source = json.loads(path.read_text("utf-8"))
        if source["commit"] != TOOL_COMMIT or len(source["tools"]) != 10:
            raise ValueError("Unexpected fixed source tool set")
        tools = {item["function"]["name"]: item for item in source["tools"]}
        if len(tools) != 10:
            raise ValueError("Duplicate fixed source tool name")
        for tool in tools.values():
            Draft202012Validator.check_schema(tool["function"]["parameters"])
        return tools
    except (OSError, ValueError, KeyError, TypeError, SchemaError) as error:
        raise RuntimeError("无法读取固定源编辑器工具合同") from error


def builtin_tool(name: str) -> dict:
    """调用者得到独立副本，不能削弱后续请求的服务端验证。"""
    try:
        return deepcopy(_builtin_tools()[name])
    except KeyError:
        raise ValueError("Unsupported editor tool") from None


class CanvasToolProtocol(CanvasGenerationProtocol):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        alias_generator=CanvasGenerationProtocol.model_config["alias_generator"],
        populate_by_name=True,
    )


class CanvasToolImage(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(pattern=r"^resource:[1-9][0-9]*$", max_length=30)

    @field_validator("url")
    @classmethod
    def permanent_identity(cls, value: str) -> str:
        parse_identifier(value.removeprefix("resource:"))
        return value


class CanvasToolTextPart(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["text"]
    text: str = Field(max_length=128000)


class CanvasToolImagePart(InputModel):
    type: Literal["image_url"]
    image_url: CanvasToolImage


ToolPart = Annotated[CanvasToolTextPart | CanvasToolImagePart, Field(discriminator="type")]


class CanvasToolMessage(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "user", "assistant"]
    content: str | Annotated[list[ToolPart], Field(min_length=1, max_length=32)]

    @field_validator("content")
    @classmethod
    def text_size(cls, value):
        if isinstance(value, str) and len(value) > 128000:
            raise ValueError("Editor tool message exceeds the text limit")
        return value


class CanvasToolFunction(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=4096)
    parameters: dict[str, JsonValue]
    strict: bool = True

    @model_validator(mode="after")
    def fixed_schema(self):
        expected = builtin_tool(self.name)["function"]
        actual_schema = json.dumps(self.parameters, sort_keys=True, allow_nan=False)
        expected_schema = json.dumps(expected["parameters"], sort_keys=True, allow_nan=False)
        if actual_schema != expected_schema or self.strict is not True:
            raise ValueError("Editor tool parameters must match the fixed source schema")
        return self


class CanvasToolDefinition(InputModel):
    type: Literal["function"]
    function: CanvasToolFunction


class CanvasToolChoice(CanvasToolProtocol):
    type: Literal["function"]
    name: str = Field(min_length=1, max_length=64)


class CanvasToolCanonical(CanvasToolProtocol):
    messages: list[CanvasToolMessage] = Field(min_length=1, max_length=100)
    tools: list[CanvasToolDefinition] = Field(min_length=1, max_length=1)
    tool_choice: Literal["auto", "required"] | CanvasToolChoice
    system_prompt: str = Field(default="", max_length=128000)

    @model_validator(mode="after")
    def coherent_request(self):
        if isinstance(self.tool_choice, CanvasToolChoice):
            if self.tool_choice.name != self.tools[0].function.name:
                raise ValueError("Editor tool choice must select the requested tool")
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("Editor tool request requires a user message")
        if len(self.image_keys()) > 16:
            raise ValueError("Editor tool supports at most 16 saved images")
        raw = json.dumps(self.model_dump(by_alias=True), ensure_ascii=False, allow_nan=False)
        if len(raw.encode("utf-8")) > TOOL_REQUEST_BYTES:
            raise ValueError("Editor tool request exceeds the byte limit")
        return self

    def image_keys(self) -> list[str]:
        return list(
            dict.fromkeys(
                part.image_url.url
                for message in self.messages
                if isinstance(message.content, list)
                for part in message.content
                if isinstance(part, CanvasToolImagePart)
            )
        )


class CanvasToolRequests(CanvasToolProtocol):
    canonical: CanvasToolCanonical


class CanvasToolReferences(InputModel):
    """工具可为纯文本；有图片时继续复用普通文字引用的身份和MIME合同。"""

    mode: Literal["text"]
    references: list[CanvasTextReference] = Field(default_factory=list, max_length=16)


class CanvasToolCallFunction(InputModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)
    arguments: str = Field(min_length=2, max_length=TOOL_REPLY_BYTES)


class CanvasToolCall(CanvasToolProtocol):
    id: str = Field(default="", max_length=512)
    type: Literal["function"] = "function"
    function: CanvasToolCallFunction
    thought_signature: str | None = Field(default=None, max_length=128000)


class CanvasToolReply(CanvasToolProtocol):
    content: str = Field(default="", max_length=TOOL_REPLY_BYTES)
    tool_calls: list[CanvasToolCall] = Field(default_factory=list, max_length=1)
    reasoning: str | None = Field(default=None, max_length=TOOL_REPLY_BYTES)


def validate_tool_arguments(name: str, raw: str) -> dict:
    """始终用服务端固定schema；输出中的非有限数字、重复字段和超限字节明确拒绝。"""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > TOOL_REPLY_BYTES:
        raise ValueError("Editor tool reply exceeds the byte limit")

    def finite_constant(_value):
        raise ValueError("Editor tool JSON must contain finite values")

    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Editor tool JSON contains duplicate fields")
            value[key] = item
        return value

    value = json.loads(raw, parse_constant=finite_constant, object_pairs_hook=unique_fields)
    if not isinstance(value, dict):
        raise ValueError("Editor tool reply must be a JSON object")
    validator = Draft202012Validator(builtin_tool(name)["function"]["parameters"])
    if not validator.is_valid(value):
        raise ValueError("Editor tool reply violates the fixed source schema")
    return value
