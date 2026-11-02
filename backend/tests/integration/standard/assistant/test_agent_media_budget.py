"""Real runtime, SDK and MySQL media admission; object storage and provider are local doubles."""

import base64
import hashlib
import io
import json
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.agent.context.budget import estimate_input_tokens
from short_drama.agent.resources.contracts import assert_reference_payload
from short_drama.agent.resources.resolver import ResourceResolver
from short_drama.agent.runtime import executor
from short_drama.agent.runtime.model_ledger import RunModelLedger
from short_drama.domain import AIModelConfig, MediaFile
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall, AgentTurn
from short_drama.service.ai_config.management.assistant_models import AgentModelService
from tests.integration.standard.assistant.test_assistant_settings import update
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    image_group_workspace,
    task_actor,
    task_service,
)
from tests.support.assistant_http import provider as provider
from tests.support.assistant_runtime import RuntimeTransport, calls
from tests.unit.standard.agent.test_harness_resources import ObjectStore

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "detail,instruction_chars,history_chars,admitted",
    [("high", 5000, 6600, True), ("low", 5000, 6600, True), ("high", 7900, 0, False)],
    ids=["high-reclaimable", "low-reclaimable", "high-irreducible"],
)
def test_runtime_accounts_for_current_image_when_summarizing_or_rejecting(
    harness_workspace, monkeypatch, detail, instruction_chars, history_chars, admitted
):
    factory, settings, model_id = harness_workspace
    reference, groups = image_group_workspace(harness_workspace)
    pixels = io.BytesIO()
    Image.new("RGB", (8, 8)).save(pixels, format="JPEG")
    jpeg = pixels.getvalue()
    assert len(jpeg) == 631
    item = groups[0]["items"][0]
    storage = ObjectStore()
    with factory.begin() as session:
        media = session.get(MediaFile, int(item["media_id"]))
        media.byte_size = len(jpeg)
        media.checksum_sha256 = hashlib.sha256(jpeg).hexdigest()
        media.format_code = "image/jpeg"
        storage.objects[media.storage_locator] = jpeg
    with factory() as session:
        session.info["actor"] = task_actor()
        version = session.get(AIModelConfig, model_id).row_version
        session.rollback()
        AgentModelService(session, settings).patch_inputs(
            model_id,
            {
                "row_version": str(version),
                "image": True,
                "audio": False,
                "context_window_tokens": 32768,
            },
        )
        update(session, settings, custom_instructions="核" * instruction_chars)
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "先核对策略，再核对此图位像素，保留原目标。",
                "model_config_id": model_id,
                "execution_target": {"mode": "standard", "project_id": reference["project_id"]},
                "subject_selection": {"expected_input_revision": "0", "subjects": [reference]},
            },
            uuid4().hex,
        )
    arguments = {
        "project_id": reference["project_id"],
        "target_kind": reference["kind"],
        "target_id": reference["id"],
        "group_id": groups[0]["id"],
        "item_id": item["id"],
        "media_id": item["media_id"],
        "purpose": "核对图像",
        "detail": detail,
    }
    transport = RuntimeTransport(
        [
            {
                **calls(("policy", "runtime__get_generation_policy", {})),
                "text": "h" * history_chars,
            },
            calls(("pixels", "workbench__read_image_content", arguments)),
            {"text": "旧排演过程已压缩，保留核对图像的完整调用与结果。"},
            {"text": "已核对真实图位像素并保留原目标。"},
        ],
        selections=[["runtime__get_generation_policy", "workbench__read_image_content"]] * 4,
    )
    admissions, original = [], RunModelLedger.before_send

    def capture(ledger, request):
        admissions.append(
            (request["source"], estimate_input_tokens(request["body"], references=True))
        )
        return original(ledger, request)

    monkeypatch.setattr(RunModelLedger, "before_send", capture)
    executor.AgentRuntime(
        factory, settings, transport=transport, resolver_factory=lambda: ResourceResolver(storage)
    ).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        if not admitted:
            assert run.status == "failed" and run.error["code"] == "agent_context_budget_exhausted"
            assert not any("data:image/" in json.dumps(body) for body in transport.requests)
            return
        assert run.status == "succeeded", json.dumps([run.error, admissions])
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(turn.usage["source"] == "summarization" for turn in turns) == 1, admissions
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests)
        for turn in turns:
            assert_reference_payload(turn.request_messages)
        reads = session.scalars(
            select(AgentToolCall).where(
                AgentToolCall.run_id == run.id,
                AgentToolCall.tool_name == "workbench__read_image_content",
            )
        ).all()
        assert len(reads) == 1 and len(reads[0].result["resource_deliveries"]) == 1
        delivered = reads[0].result["resource_deliveries"][0]["resource"]
    mains = [body for body in transport.main_requests if body.get("tools")]
    assert len(mains) == 3
    wire = json.dumps(mains[-1], ensure_ascii=False)
    assert "h" * history_chars not in wire and "先核对策略，再核对此图位像素" in wire
    image = next(
        block
        for message in mains[-1]["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "image_url"
    )
    data = base64.b64decode(image["image_url"]["url"].split(",", 1)[1])
    assert hashlib.sha256(data).hexdigest() == delivered["checksum"]
    with Image.open(io.BytesIO(data)) as result:
        assert result.size == (8, 8) and result.getpixel((0, 0)) == (0, 0, 0)
    assert all(amount <= 18432 for source, amount in admissions if source == "assistant")


def test_runtime_summary_does_not_rehydrate_the_old_pinned_parallel_image_group(
    harness_workspace, monkeypatch
):
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from tests.integration.standard.assistant.test_assistant_skills import _Storage, create_skill

    factory, settings, model_id = harness_workspace
    reference, groups = image_group_workspace(harness_workspace)
    skill = create_skill(
        (*harness_workspace, _Storage(factory)),
        instructions="保留已核对像素与最新策略。",
        resources=[],
    )
    storage, item = ObjectStore(), groups[0]["items"][0]
    with factory.begin() as session:
        media = session.get(MediaFile, int(item["media_id"]))
        data = storage.objects["minio://test/original.png"]
        media.byte_size = len(data)
        media.checksum_sha256 = hashlib.sha256(data).hexdigest()
        media.format_code = "image/png"
        storage.objects[media.storage_locator] = data
    with factory() as session:
        session.info["actor"] = task_actor()
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "核对图片后再读取最新策略。",
                "model_config_id": model_id,
                "execution_target": {"mode": "standard", "project_id": reference["project_id"]},
                "subject_selection": {"expected_input_revision": "0", "subjects": [reference]},
            },
            uuid4().hex,
        )
    arguments = {
        "project_id": reference["project_id"],
        "target_kind": reference["kind"],
        "target_id": reference["id"],
        "group_id": groups[0]["id"],
        "item_id": item["id"],
        "media_id": item["media_id"],
        "purpose": "核对图像",
        "detail": "high",
    }
    original, after_pixels = executor.assistant_middleware, []

    def compact(**kwargs):
        middleware = original(**kwargs)
        middleware[1] = SkillSummarizationMiddleware(
            model=kwargs["model"], trigger=("messages", 3), keep=("messages", 1)
        )
        return middleware

    def supplement(_body, number):
        if number == 2:
            after_pixels.append(storage.reads)
            with factory() as session:
                task_service(session, settings).send_task_message(
                    conversation.id,
                    {"task_id": accepted.task.id, "content": "保留当前策略结论，继续原目标。"},
                    uuid4().hex,
                )

    mains = []

    def reply(body):
        if not body.get("tools"):
            return {"text": "已核对图片和当前策略，继续原目标。"}
        mains.append(body)
        if len(mains) == 1:
            return calls(
                (
                    "load",
                    "runtime__load_skill",
                    {"name": skill.name, "content_version": "1", "purpose": "核对图片和策略"},
                ),
                ("pixels", "workbench__read_image_content", arguments),
            )
        if len(mains) == 2:
            return calls(("policy", "runtime__get_generation_policy", {}))
        return {"text": "已保留当前策略并完成原目标。"}

    transport = RuntimeTransport(
        [reply] * 8,
        before_reply=supplement,
        selections=[["runtime__get_generation_policy", "workbench__read_image_content"]] * 4,
    )
    monkeypatch.setattr(executor, "assistant_middleware", compact)
    executor.AgentRuntime(
        factory, settings, transport=transport, resolver_factory=lambda: ResourceResolver(storage)
    ).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == run.id)).all()
        assert sum(turn.usage["source"] == "summarization" for turn in turns) == 1
        assert run.usage["decision_calls"] == len(turns) == len(transport.requests)
        for turn in turns:
            assert_reference_payload(turn.request_messages)
    assert len(mains) == 3
    assert "data:image/" in json.dumps(mains[1])
    assert not any(
        call["id"] == "policy"
        for message in mains[-1]["messages"]
        for call in message.get("tool_calls", [])
    ), "本例必须确实摘要掉较新的AI工具组"
    assert "data:image/" not in json.dumps(mains[-1])
    assert after_pixels == [storage.reads]


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
@pytest.mark.parametrize("scenario", ["current", "stale", "summarized-stale"])
def test_current_media_budget_triggers_native_summary_through_each_sdk(
    harness_workspace, provider, protocol, scenario, monkeypatch
):
    import asyncio

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from short_drama.agent.gateway.langchain import build_chat_model
    from short_drama.agent.resources.contracts import HydrationIntent, ResourceRef
    from short_drama.agent.resources.graph import GraphResources
    from short_drama.agent.runtime.agent import build_agent
    from short_drama.agent.runtime.middleware import SkillSummarizationMiddleware
    from short_drama.agent.tools.langchain import create_langchain_tool
    from short_drama.agent.tools.registry import get_spec
    from short_drama.ai.gateway.transport import SafeTransport
    from short_drama.ai.prompts.registry import assistant_prompt
    from short_drama.schemas.assistant.operations.image import ReadImageContent
    from short_drama.schemas.assistant.operations.tool_inputs import LoadSkill
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime
    from tests.integration.standard.assistant.test_global_assistant_tasks import new_task
    from tests.support.assistant_http import response
    from tests.unit.standard.agent.test_assistant_model_protocols import (
        SETTINGS,
        claude_response,
        configured,
    )

    if scenario != "current":
        factory, settings, _ = harness_workspace
        with factory() as session:
            session.info["actor"] = task_actor()
            update(session, settings, custom_instructions="核" * 9000)
    _, accepted = new_task(harness_workspace)
    saver, context = runtime(harness_workspace, accepted)
    base, wire = provider
    wire["responses"] = [
        (200, claude_response() if protocol == "claude" else response(protocol, text="understood"))
        for _ in range(2)
    ]
    with context.factory.begin() as session:
        run = session.get(AgentRun, context.run_id)
        snapshot = run.config_snapshot
        provider_snapshot = {**snapshot, **configured(base, protocol)}
        for configured_snapshot in (snapshot, provider_snapshot):
            configured_snapshot["capability_cache"] = {
                "agent_inputs": {
                    **{
                        field: configured_snapshot.get(field)
                        for field in ("row_version", "model_key", "base_url", "credential_identity")
                    },
                    "context_window_tokens": 32768,
                }
            }
        run.config_snapshot = snapshot
    store = ObjectStore()
    resolver = ResourceResolver(store)
    frozen = resolver.freeze(
        HydrationIntent(
            resource=ResourceRef(
                kind="image",
                ref_id="image:1",
                uri="minio://test/original.png",
                source="task_attachment",
                checksum=hashlib.sha256(store.objects["minio://test/original.png"]).hexdigest(),
            ),
            purpose="核对像素",
            detail="high",
            invocation_id="turn:1",
            tool_call_id="pixels",
        )
    )
    resources = GraphResources(context, provider_snapshot, lambda: resolver)
    # 授权已由上方完整runtime用例覆盖；此组仅隔离协议计量，媒体仍走真实冻结/校验读取。
    monkeypatch.setattr(resources, "_authorize", lambda *_: None)
    model = build_chat_model(
        provider_snapshot,
        "fixture-key",
        callbacks=RunModelLedger(context).callbacks(
            stream=False,
            hydrate=resources.hydrate,
            project_input=resources.project_input,
            input_scope=resources.input_scope,
        ),
        transport=SafeTransport(SETTINGS),
    )
    names = [
        "runtime__ask_user",
        "runtime__wait_generation",
        "execute",
        "runtime__load_skill",
        "runtime__search_skills",
        "runtime__release_skill",
        "runtime__read_skill_resource",
        "runtime__materialize_skill_resource",
        "runtime__import_resources",
        "runtime__export_artifacts",
        "workbench__read_image_content",
    ]
    tools = [create_langchain_tool(get_spec(name)) for name in names]
    middleware = SkillSummarizationMiddleware(
        model=model,
        **(
            {"trigger": ("messages", 2), "keep": ("messages", 1)}
            if scenario == "summarized-stale"
            else {}
        ),
    )
    graph = build_agent(
        model=model,
        tools=tools,
        middleware=[middleware],
        checkpointer=saver,
        system_prompt=assistant_prompt("核" * (6576 if scenario == "current" else 9000)),
    )
    args = {
        "project_id": "1",
        "target_kind": "asset",
        "target_id": "2",
        "group_id": "3",
        "item_id": "4",
        "media_id": "5",
        "purpose": "核对像素",
        "detail": "high",
    }
    args = ReadImageContent.model_validate(args).model_dump(mode="json")
    initial = HumanMessage(content="h" * 6600 if scenario == "current" else "核对图片与策略")
    image_call = AIMessage(
        content="",
        id="old-group",
        tool_calls=[{"name": "workbench__read_image_content", "id": "pixels", "args": args}],
    )
    image_result = ToolMessage(
        name="workbench__read_image_content",
        tool_call_id="pixels",
        id="old-img",
        content=json.dumps(
            {"hydration_intents": [intent.model_dump(mode="json") for intent in frozen]}
        ),
    )
    messages = [initial, AIMessage(content="旧排演可压缩。"), image_call, image_result]
    activations = []
    if scenario != "current":
        load_args = LoadSkill.model_validate(
            {"name": "方法", "content_version": "1", "purpose": "校对"}
        ).model_dump(mode="json")
        image_call.tool_calls.insert(
            0, {"name": "runtime__load_skill", "id": "load", "args": load_args}
        )
        messages = [
            initial,
            image_call,
            ToolMessage(
                content=json.dumps({"body_operation_id": "100", "instructions": "核对已保存内容"}),
                name="runtime__load_skill",
                tool_call_id="load",
                id="old-load",
            ),
            image_result,
            AIMessage(
                content="",
                id="current-group",
                tool_calls=[{"name": "runtime__get_generation_policy", "id": "policy", "args": {}}],
            ),
            ToolMessage(
                content="{}",
                name="runtime__get_generation_policy",
                tool_call_id="policy",
                id="current-policy",
            ),
            *(
                [HumanMessage(content="保留当前策略结论。", id="new-input")]
                if scenario == "summarized-stale"
                else []
            ),
        ]
        activations = [
            {
                "id": "41",
                "active": True,
                "body_operation_id": "100",
                "scope_id": str(context.scope_id),
            }
        ]

        def no_stale_reads():
            pytest.fail("已过时的活动Skill并行图片组不应重新读取存储")

        resources.resolver_factory = no_stale_reads
    messages.append(
        HumanMessage(
            content="当前目标：核对已读取图片与当前策略。",
            id="goal",
            additional_kwargs={"task_context_pinned": True},
        )
    )
    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": messages,
                "skill_activations": activations,
            },
            {"configurable": {"thread_id": saver.thread_id}},
            context=context,
        )
    )
    assert result["messages"][-1].text == "understood"
    summary_count = int(scenario != "stale")
    assert len(wire["calls"]) == 1 + summary_count
    assert "h" * 6600 not in json.dumps(wire["calls"][-1][2])
    assert (
        sum(
            message.additional_kwargs.get("lc_source") == "summarization"
            for message in result["messages"]
        )
        == summary_count
    )
    if scenario != "current":
        assert "data:image/" not in json.dumps(wire["calls"][-1][2])
        assert {"old-group", "old-load", "old-img"}.issubset(
            message.id for message in result["messages"]
        )
        assert ("current-group" in {message.id for message in result["messages"]}) == (
            scenario == "stale"
        )
    with context.factory() as session:
        turns = session.scalars(select(AgentTurn).order_by(AgentTurn.turn_no)).all()
        assert [turn.usage["source"] for turn in turns] == [
            *(["summarization"] if summary_count else []),
            "assistant",
        ]
        assert all(turn.request_messages for turn in turns)
        assert session.get(AgentRun, context.run_id).usage["decision_calls"] == 1 + summary_count
        for turn in turns:
            assert_reference_payload(turn.request_messages)
