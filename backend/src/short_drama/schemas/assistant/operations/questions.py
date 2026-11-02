"""One bounded question schema for tools, saved cards and answer validation."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    Field,
    StrictBool,
    StrictStr,
    model_validator,
)

from short_drama.schemas.base import Identifier, InputModel, ReadModel, nonblank

Label = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(nonblank)]


class QuestionOption(InputModel):
    value: Label
    label: Label
    recommended: StrictBool = False
    description: Annotated[str, Field(max_length=200)] | None = None


class QuestionField(InputModel):
    key: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]
    label: Label
    type: Literal["single_select", "multi_select"]
    required: StrictBool = False
    placeholder: Annotated[str, Field(max_length=200)] | None = None
    options: Annotated[list[QuestionOption], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def selection_options(self):
        if len({item.value for item in self.options}) != len(self.options):
            raise ValueError("选项 value 不能重复")
        recommendations = sum(item.recommended for item in self.options)
        if not recommendations or self.type == "single_select" and recommendations != 1:
            raise ValueError("单选题须推荐一项，多选题须至少推荐一项")
        return self


class AskUserRequest(InputModel):
    title: Label
    question: Annotated[str, Field(min_length=1, max_length=4000), AfterValidator(nonblank)]
    fields: Annotated[list[QuestionField], Field(min_length=1, max_length=12)]
    allow_skip: StrictBool = True

    @model_validator(mode="after")
    def unique_fields(self):
        if len({item.key for item in self.fields}) != len(self.fields):
            raise ValueError("字段 key 不能重复")
        return self


class AskUserAnswer(InputModel):
    question_version: Identifier
    outcome: Literal["answered", "skipped"]
    values: dict[str, StrictStr | list[StrictStr] | None]
    additional_text: Annotated[str, Field(max_length=8000)] = ""


def validate_answer(question: AskUserRequest, answer: AskUserAnswer) -> None:
    fields = {item.key: item for item in question.fields}
    if answer.values.keys() - fields.keys():
        raise ValueError("回答包含未知字段")
    if answer.outcome == "skipped":
        if not question.allow_skip or answer.values:
            raise ValueError("此问题不能跳过，跳过时 values 必须为空")
        return
    for key, field in fields.items():
        value = answer.values.get(key)
        empty = value is None or isinstance(value, str) and not value.strip() or value == []
        if empty and field.required:
            raise ValueError(f"必填字段未回答：{key}")
        if value is None:
            continue
        choices = {item.value for item in field.options}
        if field.type == "single_select":
            valid = isinstance(value, str) and bool(value.strip()) and len(value) <= 8000
        else:
            valid = (
                isinstance(value, list)
                and len(value) <= len(choices) + 1
                and len(set(value)) == len(value)
                and all(item.strip() and len(item) <= 8000 for item in value)
                and len(set(value) - choices) <= 1
            )
        if not valid:
            raise ValueError(f"字段回答类型、长度或选项无效：{key}")


class QuestionAnswerReceipt(ReadModel):
    operation_id: Identifier
    answer_message_id: Identifier
    run_id: Identifier
    cursor: int


class PendingQuestion(ReadModel):
    operation_id: Identifier
    run_id: Identifier
    scope_id: Identifier
    parent_tool_call_id: Identifier | None
    question_version: Identifier
    request: AskUserRequest | None
    status: Literal["pending", "answered", "skipped", "cancelled"]
    can_answer: bool
    answer_message_id: Identifier | None = None
    answered_at: datetime | None = None
