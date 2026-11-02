"""Bounded schemas and the real ToolNode's hidden runtime boundary."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from short_drama.agent.tools.ask_user.ask_user import runtime__ask_user
from short_drama.schemas.assistant.operations.questions import (
    AskUserAnswer,
    AskUserRequest,
    validate_answer,
)
from tests.support.assistant_model import ScriptedModel


def question(kind="single_select", **changes):
    return AskUserRequest(
        title="问题",
        question="请补充",
        fields=[
            {
                "key": "value",
                "label": "回答",
                "type": kind,
                "required": True,
                "options": [
                    {"value": "yes", "label": "是", "recommended": True},
                    {"value": "no", "label": "否"},
                ],
                **changes,
            }
        ],
    )


@pytest.mark.parametrize(
    "kind,value",
    [
        ("single_select", True),
        ("single_select", 2),
        ("single_select", ["yes"]),
        ("single_select", " "),
        ("single_select", "a" * 8001),
        ("multi_select", "yes"),
        ("multi_select", ["yes", "yes"]),
        ("multi_select", ["yes", " "]),
        ("multi_select", ["other one", "other two"]),
    ],
)
def test_answer_rejects_wrong_type_blank_duplicate_or_multiple_other_values(kind, value):
    with pytest.raises((ValueError, ValidationError)):
        validate_answer(
            question(kind),
            AskUserAnswer(question_version="1", outcome="answered", values={"value": value}),
        )


@pytest.mark.parametrize(
    "field",
    [
        {"key": "a", "label": " ", "type": "text"},
        {"key": "1bad", "label": "x", "type": "text"},
        {"key": "a", "label": "x", "type": "single_select"},
        {"key": "a", "label": "x", "type": "text", "options": [{"value": "x", "label": "x"}]},
    ],
)
def test_question_rejects_unrenderable_field_definitions(field):
    with pytest.raises(ValidationError):
        AskUserRequest(title="标题", question="问题", fields=[field])


def test_choice_questions_accept_single_multi_and_user_written_other_answers():
    request = question()
    second = question("multi_select").fields[0].model_copy(update={"key": "mood"})
    request = request.model_copy(update={"fields": [request.fields[0], second]})
    validate_answer(
        request,
        AskUserAnswer(
            question_version="1",
            outcome="answered",
            values={"value": "我希望保留开放式结尾", "mood": ["yes", "no", "雨夜氛围"]},
        ),
    )
    assert request.fields[0].options[0].recommended is True
    assert request.fields[1].type == "multi_select"


@pytest.mark.parametrize("recommended", [[False, False], [True, True]])
def test_single_choice_requires_exactly_one_recommended_option(recommended):
    with pytest.raises(ValidationError):
        question(
            options=[
                {"value": "yes", "label": "是", "recommended": recommended[0]},
                {"value": "no", "label": "否", "recommended": recommended[1]},
            ]
        )


def test_toolnode_rejects_extra_arguments_before_question_registration():
    schema = runtime__ask_user.tool_call_schema
    assert "runtime" not in schema["properties"]
    request = question().model_dump(mode="json")
    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "invalid",
                        "name": "runtime__ask_user",
                        "args": {**request, "scope_id": "forged"},
                    }
                ],
            ),
            AIMessage(content="参数无效"),
        ]
    )
    graph = create_agent(model, tools=[runtime__ask_user])
    # Runtime dependency access would raise AttributeError; the strict original DTO rejects first.
    result = asyncio.run(
        graph.ainvoke({"messages": [HumanMessage(content="提问")]}, context=SimpleNamespace())
    )
    assert result["messages"][-2].status == "error"
    assert "scope_id" in result["messages"][-2].content
    assert result["messages"][-1].content == "参数无效"
