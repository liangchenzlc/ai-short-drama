"""Real middleware and SDK admission for large, legal Skill resource read groups."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from langchain.agents.factory import _chain_async_model_call_handlers
from langchain.agents.middleware import ModelRetryMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)
from langchain_core.messages.utils import get_buffer_string
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from langgraph.runtime import Runtime

from short_drama.agent.context.budget import (
    estimate_input_tokens,
    input_limit,
    provider_budget,
    wire_tool_counts,
)
from short_drama.agent.gateway.contracts import AgentGatewayError, ModelCallbacks
from short_drama.agent.gateway.langchain import build_chat_model
from short_drama.agent.resources.graph import GraphResources
from short_drama.agent.runtime.agent import (
    FIXED_TOOLS,
    assistant_middleware,
    bound_model_middleware,
    delegation_middleware,
)
from short_drama.agent.runtime.checkpointer import MySQLCheckpointSaver
from short_drama.agent.runtime.context import model_compaction
from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
from short_drama.agent.tools.langchain import create_langchain_tool
from short_drama.agent.tools.registry import get_spec
from short_drama.ai.prompts.registry import assistant_prompt
from short_drama.service.assistant.settings.preferences import DEFAULT_INSTRUCTIONS
from tests.integration.standard.assistant.test_agent_runtime_live import long_materials
from tests.support.assistant_model import ScriptedModel
from tests.support.assistant_runtime import RuntimeTransport


def test_retry_commits_only_successful_summary_for_replay(record_property):
    async def invoke():
        model = ScriptedModel(
            replies=[
                AIMessage(content="summary-attempt-one"),
                AIMessage(content="summary-attempt-two"),
            ]
        )
        retry = next(
            item
            for item in assistant_middleware(
                model=model,
                tools=[],
                snapshot={
                    "assistant_settings": {"tool_selector_prompt": "unused", "model_call_limit": 9}
                },
            )
            if isinstance(item, ModelRetryMiddleware)
        )
        summary = SkillSummarizationMiddleware(
            model=model, trigger=("messages", 2), keep=("messages", 1)
        )
        middleware = bound_model_middleware([summary, retry])
        chain = _chain_async_model_call_handlers([item.awrap_model_call for item in middleware])
        messages = [
            HumanMessage(content="Old request", id="history-human"),
            AIMessage(content="Old answer", id="history-ai"),
            HumanMessage(content="Current request", id="current-human"),
        ]
        request = ModelRequest(
            model=model, messages=messages, state={"messages": messages}, runtime=Runtime()
        )
        attempts = []

        async def handler(current):
            attempts.append(list(current.messages))
            if len(attempts) == 1:
                raise AgentGatewayError("invalid_agent_tool_calls", requests=1)
            return ModelResponse(result=[AIMessage(content="Complete", id="final-answer")])

        token = model_compaction.set([])
        try:
            response = await chain(request, handler)
            pending = model_compaction.get()
            saved_history = messages_to_dict(pending)
            saved_result = messages_to_dict(response.model_response.result)
            command_messages = response.commands[-1].update["messages"]
            live = add_messages(messages, command_messages)
            replay = add_messages(
                messages,
                [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    *messages_from_dict(saved_history),
                    *messages_from_dict(saved_result),
                ],
            )
            live_summaries = [
                item for item in live if item.additional_kwargs.get("lc_source") == "summarization"
            ]
            replay_summaries = [
                item
                for item in replay
                if item.additional_kwargs.get("lc_source") == "summarization"
            ]
            record_property(
                "summary_retry",
                json.dumps(
                    {
                        "main_calls": len(attempts),
                        "summary_calls": len(model.requests),
                        "success_commands": len(response.commands),
                        "pending_messages": len(pending),
                        "live_summaries": len(live_summaries),
                        "replay_summaries": len(replay_summaries),
                    }
                ),
            )
            assert len(attempts) == len(model.requests) == 2
            assert len(response.commands) == 1
            assert len(pending) == len(attempts[-1]) == 2
            assert messages_to_dict(pending) == messages_to_dict(command_messages[1:-1])
            assert messages_to_dict(pending) == messages_to_dict(attempts[-1])
            assert len(live_summaries) == len(replay_summaries) == 1
            assert live_summaries[0].content.endswith("summary-attempt-two")
            assert replay_summaries[0].content == live_summaries[0].content
            assert all("summary-attempt-one" not in item.content for item in replay)
        finally:
            model_compaction.reset(token)

    asyncio.run(invoke())


def summary_fixture():
    scenes = long_materials()
    names = [
        "runtime__load_skill",
        "runtime__read_skill_resource",
        "runtime__release_skill",
        "runtime__search_skills",
        "runtime__describe_creation_target",
        "runtime__read_task_results",
        "runtime__materialize_skill_resource",
        "runtime__read_resource",
        "runtime__read_operation_receipt",
        "workbench__read_current_script",
        "workbench__apply_standard_patch",
        "workbench__read_storyboard",
        "runtime__propose_script_patch",
        "execute",
        "write_file",
        "read_file",
    ]
    tools = [create_langchain_tool(get_spec(name)) for name in names]
    pinned, activations = [], []
    for index in range(2):
        activations.append(
            {"id": str(index + 1), "active": True, "body_operation_id": f"body-{index}"}
        )
        manifest = [
            {
                "path": f"references/scene-{number + 1:02d}.md",
                "kind": "text",
                "byte_size": len(scene["content"].encode()),
                "checksum_sha256": hashlib.sha256(scene["content"].encode()).hexdigest(),
                "bucket": "controlled",
                "object_name": f"synthetic/{number + 1}",
            }
            for number, scene in enumerate(scenes)
            if number // 21 == index
        ]
        pinned.extend(
            [
                AIMessage(
                    content="",
                    id=f"load-ai-{index}",
                    tool_calls=[
                        {
                            "id": f"load-{index}",
                            "name": "runtime__load_skill",
                            "args": {
                                "name": f"雨夜排演资料{index + 1}",
                                "content_version": "1",
                                "purpose": "取得42幕资料",
                            },
                        }
                    ],
                ),
                ToolMessage(
                    content=json.dumps(
                        {
                            "activation_id": str(index + 1),
                            "body_operation_id": f"body-{index}",
                            "instructions": "逐幕保留五项证据、24项调度与前幕真实ID，读回核验。",
                            "resources": manifest,
                        },
                        ensure_ascii=False,
                    ),
                    id=f"load-result-{index}",
                    name="runtime__load_skill",
                    tool_call_id=f"load-{index}",
                ),
            ]
        )
    return scenes, tools, pinned, activations


def reads(scenes, indices, group):
    calls, results = [], []
    for index in indices:
        call_id = f"{group}-{index}"
        content = scenes[index]["content"]
        assert len(content) <= 4000
        arguments = {
            "activation_id": str(index // 21 + 1),
            "path": f"references/scene-{index + 1:02d}.md",
            "offset": 0,
            "limit": 4000,
        }
        get_spec("runtime__read_skill_resource").args_schema.model_validate(arguments)
        calls.append({"id": call_id, "name": "runtime__read_skill_resource", "args": arguments})
        results.append(
            ToolMessage(
                content=json.dumps(
                    {
                        **arguments,
                        "kind": "text",
                        "content": content,
                        "checksum_sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "byte_size": len(content.encode()),
                        "next_offset": None,
                        "receipt_operation_id": str(9000 + index),
                    },
                    ensure_ascii=False,
                ),
                name="runtime__read_skill_resource",
                id=f"result-{call_id}",
                tool_call_id=call_id,
                additional_kwargs={
                    "source_message_ids": ["original-goal"],
                    "skill_activation_ids": [str(index // 21 + 1)],
                    "subject_keys": ["project:42"],
                },
            )
        )
    return [AIMessage(content="", id=f"ai-{group}", tool_calls=calls), *results]


async def exercise(
    scenario,
    *,
    helper_window=65536,
    summary_text="保留证据、前幕真实ID与L3游标。",
    transport=None,
    observed=None,
):
    scenes, tools, pinned, activations = summary_fixture()
    history = {
        "small": reads(scenes, range(2), "small"),
        "latest": [
            *sum((reads(scenes, [i], f"old-{i}") for i in range(4)), []),
            *reads(scenes, range(4, 20), "latest"),
        ],
        "only": reads(scenes, range(16), "only"),
        "history": sum((reads(scenes, [i], f"old-{i}") for i in range(24)), []),
        "fixed": [],
    }[scenario]
    goal = HumanMessage(
        content="原目标42幕，最新更正保留真实前幕ID；L3从明确游标读回。",
        id="task-sources:42:2",
        additional_kwargs={
            "task_context_pinned": True,
            "source_message_ids": ["original-goal", "latest-correction"],
        },
    )
    if scenario == "fixed":
        goal.content += "固定内容" * 15000
    messages = [*pinned, *history, goal]
    admissions, retained = [], []
    transport = transport or RuntimeTransport([{"text": summary_text}] * 100)

    def model(window):
        snapshot = {
            "service_type": "text",
            "provider": "compatible",
            "base_url": "https://local.invalid/v1",
            "model_key": "controlled",
            "row_version": 1,
            "credential_identity": "controlled",
        }
        snapshot["capability_cache"] = {
            "agent_inputs": {**snapshot, "context_window_tokens": window}
        }

        async def admit(payload):
            record = {"source": payload["source"], "body": payload["body"], "window": window}
            admissions.append(record)
            record["budget"] = provider_budget(
                payload["body"], 8192, references=True, context_window=window
            )

        return build_chat_model(
            snapshot,
            "",
            callbacks=ModelCallbacks(
                settings=SimpleNamespace(), before_send=admit, max_output_tokens=8192, stream=False
            ),
            transport=transport,
        )

    main = model(65536)
    helper = main if helper_window == 65536 else model(helper_window)
    request = ModelRequest(
        model=main,
        messages=messages,
        system_message=SystemMessage(content="逐幕创作并真实保存。"),
        tools=tools,
        state={"messages": messages, "skill_activations": activations},
    )
    if observed is not None:
        observed.update(
            request=request,
            messages_before=messages_to_dict(messages),
            retained=retained,
            admissions=admissions,
        )

    async def handler(actual):
        retained.extend(actual.messages)
        return await main.bind_tools(actual.tools).ainvoke(
            [actual.system_message, *actual.messages]
        )

    response = await SkillSummarizationMiddleware(model=helper).awrap_model_call(request, handler)
    return SimpleNamespace(
        response=response,
        admissions=admissions,
        retained=retained,
        history=history,
        pinned=pinned,
        goal=goal,
        transport=transport,
    )


def test_deepseek_reasoning_triggers_two_summaries_across_typed_restore():
    scenes, tools, pinned, activations = summary_fixture()
    pinned[0].additional_kwargs["reasoning_content"] = "保留冻结方法的推理。"
    goal = HumanMessage(
        content="保留原目标、资料游标与完整方法组。",
        id="task-sources:reasoning",
        additional_kwargs={"task_context_pinned": True},
    )
    snapshot = {
        "service_type": "text",
        "provider": "deepseek",
        "model_key": "controlled",
        "base_url": "https://local.invalid/v1",
        "row_version": 1,
        "credential_identity": "controlled",
    }
    snapshot["capability_cache"] = {"agent_inputs": {**snapshot, "context_window_tokens": 65536}}
    settings = SimpleNamespace()
    resources = GraphResources(SimpleNamespace(settings=settings), snapshot, None)
    admissions, main_inputs, main_results = [], [], []

    def reply(body):
        if body.get("tools"):
            return {
                "calls": [(f"main-{len(main_results)}", "runtime__describe_creation_target", {})]
            }
        return {"text": "已保留目标、资料游标与来源身份。"}

    transport = RuntimeTransport([reply] * 20)

    async def admit(payload):
        body = payload["body"]
        record = {
            "source": payload["source"],
            "thinking": body.get("thinking"),
            "wire_estimate": estimate_input_tokens(body, references=True),
        }
        if payload["source"] == "assistant":
            record["projection_estimate"] = estimate_input_tokens(
                {**body, "messages": resources.project_input(main_inputs[-1])}, references=True
            )
        admissions.append(record)
        assert body["max_tokens"] == 8192
        record["budget"] = provider_budget(body, 8192, references=True, context_window=65536)

    model = build_chat_model(
        snapshot,
        "",
        transport=transport,
        callbacks=ModelCallbacks(
            settings=settings,
            stream=False,
            before_send=admit,
            max_output_tokens=8192,
            hydrate=resources.hydrate,
            project_input=resources.project_input,
            input_scope=resources.input_scope,
        ),
    )
    saver = MySQLCheckpointSaver(SimpleNamespace(owner_id=1, run_id=2))

    async def invoke():
        restored = []
        for round_no in range(2):
            history = []
            for index in range(round_no * 4, (round_no + 1) * 4):
                group = reads(scenes, [index], f"reasoning-{round_no}-{index}")
                group[0].additional_kwargs["reasoning_content"] = "r" * 48000
                history.extend(group)
            messages = [*restored, *history] if restored else [*pinned, *history, goal]
            original = messages_to_dict(messages)
            resources.project_input(messages)
            assert messages_to_dict(messages) == original
            retained, start = [], len(admissions)
            request = ModelRequest(
                model=model,
                messages=messages,
                tools=tools,
                system_message=SystemMessage(content="逐幕创作并真实保存。"),
                state={"messages": messages, "skill_activations": activations},
            )

            async def handler(actual, *, retained=retained):
                retained.extend(actual.messages)
                main_inputs.append([actual.system_message, *actual.messages])
                result = await model.bind_tools(actual.tools).ainvoke(main_inputs[-1])
                main_results.append(result)
                return result

            try:
                await SkillSummarizationMiddleware(model=model).awrap_model_call(request, handler)
            except AgentGatewayError as error:
                raise AssertionError(
                    f"Expected summary before SDK admission: {error.code}; "
                    f"scalars={admissions[-1] if admissions else {}}"
                ) from error
            assert messages_to_dict(messages) == original
            assert any(row["source"] == "summarization" for row in admissions[start:])
            assert all(item in retained for item in pinned)
            assert retained[-1] == goal
            response = main_results[-1]
            state = {
                "messages": [
                    *retained,
                    response,
                    ToolMessage(
                        content='{"project_id":"42"}',
                        id=f"main-result-{round_no}",
                        name="runtime__describe_creation_target",
                        tool_call_id=response.tool_calls[0]["id"],
                    ),
                ]
            }
            state["messages"] = add_messages([], state["messages"])
            restored = saver._decode(*saver._encode(state))["messages"]
            loaded = next(message for message in restored if message.id == pinned[0].id)
            assert loaded.additional_kwargs["reasoning_content"] == "保留冻结方法的推理。"

    asyncio.run(invoke())
    helpers = [row for row in admissions if row["source"] == "summarization"]
    mains = [row for row in admissions if row["source"] == "assistant"]
    assert len(helpers) >= 2 and len(mains) == 2
    assert all(row["thinking"] == {"type": "disabled"} for row in helpers)
    assert all(row["thinking"] is None for row in mains)
    assert all(row["budget"]["input_limit_tokens"] == 51200 for row in admissions)
    assert len(transport.requests) == len(admissions)


@pytest.mark.parametrize("scenario", ["latest", "only", "history"])
def test_large_legal_complete_groups_and_helper_input_are_compressible(scenario):
    result = asyncio.run(exercise(scenario))
    helpers = [item for item in result.admissions if item["source"] == "summarization"]
    assert helpers and all(
        item["budget"]["estimated_input_tokens"] <= input_limit(8192, context_window=item["window"])
        for item in helpers
    )
    assert len([item for item in result.admissions if item["source"] == "assistant"]) == 1
    assert all(item in result.retained for item in result.pinned)
    assert result.retained[-1] == result.goal
    summaries = [
        item
        for item in result.retained
        if item.additional_kwargs.get("lc_source") == "summarization"
    ]
    assert len(summaries) == 1
    assert summaries[0].additional_kwargs["source_message_ids"] == ["original-goal"]
    assert summaries[0].additional_kwargs["subject_keys"] == ["project:42"]
    assert summaries[0].additional_kwargs["skill_activation_ids"] == sorted(
        {
            activation
            for message in result.history
            if message not in result.retained
            for activation in message.additional_kwargs.get("skill_activation_ids", [])
        }
    )
    assert summaries[0].additional_kwargs["summary_helper_calls"] == len(helpers)
    assert {
        call["id"]
        for message in result.retained
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    } == {item.tool_call_id for item in result.retained if isinstance(item, ToolMessage)}


def test_helper_uses_its_own_smaller_window_and_covers_every_source_fragment():
    result = asyncio.run(exercise("only", helper_window=32768))
    helpers = [item for item in result.admissions if item["source"] == "summarization"]
    assert len(helpers) > 1
    source = get_buffer_string(result.history, format="xml")
    fragments = []
    for item in helpers:
        assert item["budget"]["estimated_input_tokens"] <= input_limit(8192, context_window=32768)
        content = item["body"]["messages"][0]["content"]
        payload = json.loads(
            content.split("Messages to summarize:\n", 1)[1].rsplit("\n</messages>", 1)[0]
        )
        assert payload["offset"] == sum(len(fragment) for fragment in fragments)
        assert payload["total_chars"] == len(source)
        fragments.append(payload["history_fragment"])
    assert "".join(fragments) == source
    assert helpers[-1]["budget"]["context_window_tokens"] == 32768
    assert result.goal.content not in source


@pytest.mark.parametrize("stage", ["first", "middle", "last"])
@pytest.mark.parametrize("invalid_summary", ["", " \t\n "], ids=["empty", "whitespace"])
def test_empty_helper_fragment_rejects_without_replacing_history(stage, invalid_summary):
    fragments, failures, observed, pending = [], [], {}, []

    def reply(body):
        if body.get("tools"):
            return {"text": "错误接受空摘要后的继续答复"}
        content = body["messages"][0]["content"]
        payload = json.loads(
            content.split("Messages to summarize:\n", 1)[1].rsplit("\n</messages>", 1)[0]
        )
        fragments.append(payload)
        start = payload["offset"]
        end = start + len(payload["history_fragment"])
        failed = {
            "first": start == 0,
            "middle": 0 < start < end < payload["total_chars"],
            "last": end == payload["total_chars"],
        }[stage]
        if failed:
            failures.append(len(fragments))
        return {"text": invalid_summary if failed else "保留此前全部事实与本片游标。"}

    transport = RuntimeTransport([reply] * 100)
    token = model_compaction.set(pending)
    try:
        with pytest.raises(AgentGatewayError) as caught:
            asyncio.run(
                exercise("history", helper_window=32768, transport=transport, observed=observed)
            )
    finally:
        model_compaction.reset(token)
    assert caught.value.code == "agent_summary_empty"
    assert caught.value.requests == len(fragments)
    assert failures == [len(fragments)]
    assert len(transport.requests) == len(fragments)
    assert {item["source"] for item in observed["admissions"]} == {"summarization"}
    assert not observed["retained"] and not pending
    request = observed["request"]
    assert messages_to_dict(request.messages) == observed["messages_before"]
    assert messages_to_dict(request.state["messages"]) == observed["messages_before"]
    if stage != "first":
        assert len(fragments) > 1 and fragments[-1]["previous_summary"]


def test_prior_summary_that_cannot_fit_the_next_helper_is_explicitly_rejected():
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(exercise("only", helper_window=32768, summary_text="不可压缩正文" * 20000))
    facts = caught.value.context_budget
    assert facts["phase"] == "helper" and facts["source"] == "summarization"
    assert facts["fixed_kind"] == "helper_prompt_and_prior_summary"
    assert facts["fixed_estimated_input_tokens"] > facts["input_limit_tokens"]
    assert 0 < facts["covered_characters"] < facts["total_characters"]
    assert facts["tool_call_count"] == facts["tool_result_count"] == 16
    assert facts["complete_tool_group_count"] == 1
    assert "不可压缩正文" not in json.dumps(facts, ensure_ascii=False)


def test_small_input_does_not_generate_a_summary():
    result = asyncio.run(exercise("small"))
    assert [item["source"] for item in result.admissions] == ["assistant"]
    assert all(item in result.retained for item in result.history)


@pytest.mark.parametrize("scenario,phase", [("fixed", "fixed"), ("only", "post_summary")])
def test_incompressible_input_remains_an_explicit_safe_rejection(scenario, phase):
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(exercise(scenario, summary_text="不可压缩正文" * 20000))
    facts = caught.value.context_budget
    assert facts["phase"] == phase and facts["source"] == "assistant"
    assert facts["estimated_input_tokens"] > facts["input_limit_tokens"]
    assert facts["context_window_tokens"] == 65536 and facts["reserved_output_tokens"] == 8192
    assert facts["message_count"] > 0 and facts["tool_call_count"] >= 2
    assert "不可压缩正文" not in json.dumps(facts, ensure_ascii=False)


@pytest.mark.parametrize(
    "source,phase",
    [("assistant", "main"), ("tool_selection", "selector"), ("summarization", "helper")],
)
@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
def test_sdk_pre_send_rejection_keeps_phase_source_and_numeric_facts(source, phase, protocol):
    transport = RuntimeTransport([])
    callbacks_seen = []

    async def admit(payload):
        provider_budget(payload["body"], 8192, context_window=65536)

    async def error(payload):
        callbacks_seen.append(payload)

    model = build_chat_model(
        {
            "service_type": "text",
            "provider": "compatible",
            "base_url": "https://local.invalid/v1",
            "model_key": "controlled",
            "runtime_profile": {
                "api_format": "claude" if protocol == "claude" else "openai",
                "protocol": {
                    "chat": "chat-completion",
                    "responses": "openai-response",
                    "claude": "messages",
                }[protocol],
            },
        },
        "",
        callbacks=ModelCallbacks(
            settings=SimpleNamespace(), before_send=admit, error=error, stream=False
        ),
        transport=transport,
    )
    with pytest.raises(AgentGatewayError) as caught:
        asyncio.run(
            model.ainvoke(
                [
                    AIMessage(
                        content="",
                        tool_calls=[{"id": "read", "name": "lookup", "args": {}}],
                    ),
                    ToolMessage(content="真实受控读取结果", tool_call_id="read"),
                    HumanMessage(content="PRIVATE_INPUT_" + "雨" * 60000),
                ],
                config={"metadata": {"lc_source": source}},
            )
        )
    assert transport.requests == []
    assert len(callbacks_seen) == 1 and callbacks_seen[0]["requests"] == 0
    facts = caught.value.context_budget
    assert facts["phase"] == phase and facts["source"] == source
    assert facts["estimated_input_tokens"] > facts["input_limit_tokens"]
    assert facts["tool_call_count"] == facts["tool_result_count"] == 1
    assert facts["complete_tool_group_count"] == 1
    assert callbacks_seen[0]["context_budget"] == facts
    assert "PRIVATE_INPUT_" not in json.dumps(facts)


@pytest.mark.parametrize("force_summary", [False, True], ids=["default", "summary"])
@pytest.mark.parametrize("same_group", [False, True], ids=["independent", "canonical_sibling"])
def test_metadata_only_skill_groups_can_enter_real_summary(force_summary, same_group):
    body = "CANONICAL_BODY：逐字核对资料证据，保留动作因果。"
    description = "雨夜取证方法：" + "先检查动作因果与证据来源。" * 100 + "尾部唯一检索词"
    exclusions = "不适用于真实媒体生成。" * 90
    groups = []
    for name, activation in [("canonical", "101"), ("reload", "103"), ("already", "103")]:
        result = {
            "activation_id": activation,
            "body_operation_id": "100",
            "active": True,
            "name": "雨夜取证方法",
            "description": description,
            "exclusions": exclusions,
            "already_loaded": name == "already",
            "resources": [{"path": "references/evidence.md", "kind": "text"}],
            **(
                {"instructions": body}
                if name == "canonical"
                else {"note": "复用原正文重新激活" if name == "reload" else "正文已在当前上下文中"}
            ),
        }
        groups.append(
            [
                AIMessage(
                    content="",
                    id=f"ai-{name}",
                    tool_calls=[
                        {
                            "id": name,
                            "name": "runtime__load_skill",
                            "args": {
                                "name": "雨夜取证方法",
                                "content_version": "1",
                                "purpose": "核对重复引用" if name == "already" else "取得冻结方法",
                            },
                        }
                    ],
                ),
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    id=f"result-{name}",
                    name="runtime__load_skill",
                    tool_call_id=name,
                ),
            ]
        )
    if same_group:
        groups[0][0].tool_calls.extend(groups[1][0].tool_calls)
        groups[0].append(groups[1][1])
        groups[1] = []
    goal = HumanMessage(
        content=json.dumps(
            {
                "goal": "核对冻结方法和证据，不生成媒体。",
                "answers": [{"message_id": "answer-102", "continue": "yes"}],
                "current_skill_activations": [{"id": "103", "active": True}],
            },
            ensure_ascii=False,
        ),
        id="task-sources:1:1",
        additional_kwargs={"task_context_pinned": True},
    )
    messages = [
        *sum(groups, []),
        HumanMessage(content="已读证据：先听钟，再见钥匙。", id="old-history"),
        HumanMessage(content="最近的可保留历史。", id="recent-history"),
        goal,
    ]
    before = messages_to_dict(messages)
    transport = RuntimeTransport([{"text": "保留已核对证据及当前身份。"}] * 4)
    admissions, fragments, retained = [], [], []

    async def admit(payload):
        facts = provider_budget(payload["body"], 8192, context_window=32768)
        admissions.append((payload["source"], facts))
        if payload["source"] == "summarization":
            content = payload["body"]["messages"][0]["content"]
            value = json.loads(
                content.split("Messages to summarize:\n", 1)[1].rsplit("\n</messages>", 1)[0]
            )
            fragments.append(value["history_fragment"])

    snapshot = {
        "service_type": "text",
        "provider": "compatible",
        "base_url": "https://local.invalid/v1",
        "model_key": "controlled",
        "row_version": 1,
        "credential_identity": "controlled",
    }
    snapshot["capability_cache"] = {"agent_inputs": {**snapshot, "context_window_tokens": 32768}}
    model = build_chat_model(
        snapshot,
        "",
        callbacks=ModelCallbacks(
            settings=SimpleNamespace(), before_send=admit, max_output_tokens=8192, stream=False
        ),
        transport=transport,
    )
    delegation = delegation_middleware(
        model=model,
        child_tools=[],
        child_middleware=[],
        backend=None,
        system_prompt=assistant_prompt(DEFAULT_INSTRUCTIONS),
        task_description=get_spec("task").description,
    )
    extra = {
        "runtime__read_conversation_history",
        "runtime__read_resource",
        "runtime__read_task_results",
        "runtime__search_memory",
        "runtime__record_task_observation",
    }
    tools = [
        create_langchain_tool(get_spec(name)) for name in sorted((FIXED_TOOLS - {"task"}) | extra)
    ] + list(delegation.tools)
    request = ModelRequest(
        model=model,
        messages=messages,
        system_message=SystemMessage(content=assistant_prompt(DEFAULT_INSTRUCTIONS)),
        tools=tools,
        state={
            "messages": messages,
            "skill_activations": [
                {"id": "101", "active": False, "body_operation_id": "100"},
                {"id": "103", "active": True, "body_operation_id": "100"},
            ],
        },
    )

    async def handler(actual):
        retained.extend(actual.messages)
        return await model.bind_tools(actual.tools).ainvoke(
            [actual.system_message, *actual.messages]
        )

    async def invoke():
        options = {"trigger": ("messages", 1), "keep": ("messages", 1)} if force_summary else {}
        return await SkillSummarizationMiddleware(model=model, **options).awrap_model_call(
            request, handler
        )

    asyncio.run(invoke())
    assert messages_to_dict(messages) == before
    assert retained and all(message in retained for message in groups[0])
    assert retained[-1] == goal
    assert sum(body in message.content for message in retained) == 1
    assert [message.id for message in retained if message.id in {m.id for m in messages}] == [
        message.id for message in messages if message in retained
    ]
    counts = wire_tool_counts(
        [
            {"role": "assistant", "tool_calls": message.tool_calls}
            if isinstance(message, AIMessage)
            else {"role": "tool", "tool_call_id": message.tool_call_id}
            for message in retained
            if isinstance(message, (AIMessage, ToolMessage))
        ]
    )
    assert counts["tool_call_count"] == counts["tool_result_count"]
    assert admissions[-1][0] == "assistant"
    assert all(facts["estimated_input_tokens"] <= 18432 for _, facts in admissions)
    if force_summary:
        source = "".join(fragments)
        assert source and body not in source
        assert "正文已在当前上下文中" in source and "核对重复引用" in source
        if same_group:
            assert "复用原正文重新激活" not in source
        else:
            assert "复用原正文重新激活" in source
    else:
        assert not fragments and all(message in retained for message in sum(groups, []))
