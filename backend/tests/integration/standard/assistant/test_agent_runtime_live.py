"""Authorized opt-in text acceptance; no supplied model replies or outer per-turn driver."""

import asyncio
import hashlib
import json
import os
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from short_drama.agent.coordinator.recovery import recover
from short_drama.agent.tools.registry import get_spec
from short_drama.core.identity import token_hash
from short_drama.core.time import utcnow
from short_drama.domain import AIModelConfig, Asset, AsyncTask, ShotScript
from short_drama.domain.assistant.conversations.models import AgentEvent, AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall, AgentTurn
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.domain.identity.accounts.user import UserSession
from short_drama.service.ai_config.management.assistant_models import AgentModelService
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.assistant.settings.preferences import AssistantSettingsService
from short_drama.service.creation.assets.management.assets import AssetService
from short_drama.service.skills.catalog.skill import AgentSkillService
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    drain_assistant_media,
    media_worker,
    task_actor,
    task_service,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.support.agent_runtime_live import live_case as live_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_AGENT_RUNTIME_LIVE") != "1",
        reason="需显式启用已授权的真实文本模型及专用基础设施",
    ),
]
task_workspace = harness_workspace


def assert_success(case, accepted):
    with case.factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        assert run.usage["reserved_output_tokens"] == 0
        assert session.scalar(
            select(AgentMessage.id).where(
                AgentMessage.task_id == accepted.task.id,
                AgentMessage.role == "assistant",
                AgentMessage.kind == "reply",
            )
        )


def question_answer(operation, *, outcome="answered"):
    values = {}
    if outcome == "answered":
        for item in operation.arguments["fields"]:
            kind = item["type"]
            values[item["key"]] = (
                item["options"][0]["value"]
                if kind == "single_select"
                else [item["options"][0]["value"]]
                if kind == "multi_select"
                else True
                if kind == "boolean"
                else 3
                if kind == "number"
                else "雨夜团圆，父亲平安归来"
            )
    return {"question_version": "1", "outcome": outcome, "values": values}


def expire(case, accepted):
    with case.factory.begin() as session:
        session.get(AgentRun, accepted.run.id).lease_until = utcnow() - timedelta(seconds=1)
    assert recover(case.factory, case.settings) >= 1


def questions(case, accepted):
    return [
        item
        for item in case.operations(accepted)
        if (item.wait_state or {}).get("kind") == "user_input"
    ]


def answer_question(case, operation, *, outcome="answered"):
    payload = question_answer(operation, outcome=outcome)
    with case.question_api() as client:
        response = client.post(
            f"/assistant/operations/{operation.id}/answer",
            json=payload,
            headers={"idempotency-key": uuid4().hex},
        )
    assert response.status_code == 200, response.text
    return payload, response.json()


def invocation_ids(case, accepted):
    return {
        item.business_invocation_id
        for item in case.operations(accepted)
        if item.business_invocation_id is not None
    }


def trace_roundtrip(case, conversation, accepted):
    from short_drama.api.v1.assistant.conversations.events import conversation_events

    path = f"/assistant/conversations/{conversation.id}/runs/{accepted.run.id}/trace"
    with case.question_api() as client:
        first = client.get(path, params={"limit": 100})
        second = client.get(path, params={"limit": 100})
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
    page = first.json()
    assert len({item["block_id"] for item in page["items"]}) == len(page["items"])
    with case.factory() as session:
        rows = task_service(session, case.settings).events(conversation.id, 0)
        count = len(rows)
    assert count > 0
    with case.factory.begin() as session:
        if session.get(UserSession, 1) is None:
            session.add(
                UserSession(
                    id=1,
                    user_id=1,
                    token_hash=token_hash("acceptance-session"),
                    csrf_hash="hash",
                    expires_at=utcnow() + timedelta(days=1),
                    created_at=utcnow(),
                )
            )

    async def connected():
        return False

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_factory=case.factory, settings=case.settings, storage=case.objects
            )
        ),
        state=SimpleNamespace(actor=task_actor()),
        is_disconnected=connected,
    )

    async def replay():
        first = await conversation_events(conversation.id, request, 0, None)
        second = await conversation_events(conversation.id, request, 0, None)
        left, right = first.body_iterator, second.body_iterator
        chunks = []
        try:
            for _ in range(count):
                chunk = await asyncio.wait_for(anext(left), timeout=10)
                assert chunk == await asyncio.wait_for(anext(right), timeout=10)
                chunks.append(chunk)
        finally:
            await left.aclose()
            await right.aclose()
        split = rows[count // 2].seq
        resumed = await conversation_events(conversation.id, request, 0, str(split))
        iterator = resumed.body_iterator
        try:
            expected = [chunk for row, chunk in zip(rows, chunks, strict=True) if row.seq > split]
            for chunk in expected:
                assert chunk == await asyncio.wait_for(anext(iterator), timeout=10)
        finally:
            await iterator.aclose()

    asyncio.run(replay())
    case.facts.update(trace_refresh_equal=True, sse_replay_equal=True, sse_last_event_id=True)
    return page


@pytest.mark.parametrize("repetition", range(2))
def test_live_protocol_streaming_parallel_tools_and_actual_usage(live_case, repetition):
    case = live_case
    _conversation, accepted, _project = case.create(
        "请同时核对本作品当前目录和我的媒体生成总开关，这两项互不依赖。"
        "根据真实返回值简要说明当前能做什么，不创建或修改内容。"
    )
    case.execute(accepted)
    assert_success(case, accepted)
    operations = case.operations(accepted)
    assert {"runtime__describe_creation_target", "runtime__get_generation_policy"} <= {
        item.tool_name for item in operations if item.status == "succeeded"
    }
    with case.factory() as session:
        calls = session.scalars(
            select(AgentToolCall).where(AgentToolCall.run_id == accepted.run.id)
        ).all()
        assert max(Counter(item.turn_id for item in calls).values()) >= 2
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id)).all()
        assert all(turn.usage["provider_usage"].get("input_tokens", 0) > 0 for turn in turns)
        assert all(turn.usage["provider_usage"].get("output_tokens_reported") for turn in turns)
        assert all(turn.usage["settled"] for turn in turns)
    case.facts.update(
        parallel_tool_calling=True, streaming="actual protected SDK", media_external_requests=0
    )
    case.passed = True


def long_materials():
    locations = ["站前钟楼", "旧候车厅", "行李寄存处", "地下通道", "废弃站台", "末班车厢"]
    facets = [
        "人物动机",
        "视线方向",
        "构图中心",
        "前景遮挡",
        "景深变化",
        "镜头运动",
        "脚步节奏",
        "环境声音",
        "道具交接",
        "光源位置",
        "门窗关系",
        "时间连续性",
        "情绪转折",
        "对白停顿",
        "身体动作",
        "误解依据",
        "证据质感",
        "角色距离",
        "出口方向",
        "空间反射",
        "听觉反差",
        "场景衔接",
        "信息遮蔽",
        "下一幕悬念",
    ]
    scenes = []
    for index in range(42):
        token = uuid4().hex[:12]
        mandatory = [
            f"线索{token}",
            f"编号{index + 1:02d}铜钥匙",
            f"第{index + 3}声钟响",
            f"包裹记号{uuid4().hex[:8]}",
            f"车票暗码{uuid4().hex[:8]}",
        ]
        place = locations[index % len(locations)]
        details = [
            f"{facet}：本幕发生在{place}的第{index + 1}号区域。林晚在{(index % 4) + 1}步后停下，"
            f"乘客从{'左' if part % 2 else '右'}侧确认{mandatory[part % len(mandatory)]}。"
            f"把这一证据用{['触碰', '折叠', '照亮', '交换'][part % 4]}动作呈现，"
            f"持续{2 + (index + part) % 5}秒；"
            f"保留{['雨声', '轮轴声', '广播声'][part % 3]}作为声音桥。"
            "误会须来自可见动作与信息差，角色不能直接解释自己知道的结局。"
            for part, facet in enumerate(facets)
        ]
        scenes.append(
            {
                "title": f"第{index + 1:02d}幕·{place}",
                "mandatory": mandatory,
                "content": f"# 第{index + 1:02d}幕排演资料\n必须逐字保留的五个证据："
                + json.dumps(mandatory, ensure_ascii=False)
                + "\n"
                + "\n".join(details)
                + "\n本幕首版保存时，剧本末尾写‘前幕ID=<上一幕真实保存ID>’；"
                "第一幕写‘前幕ID=START’。"
                "不得先创建所有幕再回填；每幕首次保存就必须有已存在的前幕身份。",
            }
        )
    return scenes


def assert_long_chain_script_witness(
    operations, tool_calls, events, *, run_id, root_scope_id, skill, script
):
    """Bind the frozen second Skill script to one successful root sandbox execution."""
    checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
    calls = {item.id: item for item in tool_calls}
    bound = []
    for operation in operations:
        call = calls.get(operation.tool_call_id)
        if (
            operation.run_id == run_id
            and operation.scope_id == root_scope_id
            and operation.status == "succeeded"
            and operation.finished_at is not None
            and call is not None
            and call.run_id == run_id
            and call.scope_id == root_scope_id
            and call.status == "succeeded"
            and call.tool_name == operation.tool_name
            and call.arguments == operation.arguments
        ):
            bound.append(operation)
    loads = [item for item in bound if item.tool_name == "runtime__load_skill"]
    for materialize in bound:
        if (
            materialize.tool_name != "runtime__materialize_skill_resource"
            or materialize.arguments.get("path") != "scripts/check_chain.py"
        ):
            continue
        result = materialize.result or {}
        activation_id = str(materialize.arguments.get("activation_id", ""))
        reference = result.get("resource_ref") or {}
        path = result.get("path")
        if (
            not isinstance(path, str)
            or not path
            or str(result.get("activation_id")) != activation_id
            or reference.get("kind") != "script"
            or reference.get("source") != "skill_resource"
            or reference.get("ref_id") != f"skill-resource:{activation_id}:scripts/check_chain.py"
            or reference.get("checksum") != checksum
            or (reference.get("metadata") or {}).get("path") != "scripts/check_chain.py"
        ):
            continue
        loaded = []
        for load in loads:
            receipt = load.result or {}
            record = receipt.get("activation_record") or {}
            frozen = record.get("skill") or {}
            if (
                str(receipt.get("activation_id")) == activation_id
                and str(receipt.get("skill_id")) == str(skill.id)
                and str(receipt.get("content_version")) == str(skill.content_version)
                and str(record.get("id")) == activation_id
                and str(record.get("scope_id")) == str(root_scope_id)
                and str(record.get("task_id")) == str(materialize.task_id)
                and str(frozen.get("id")) == str(skill.id)
                and str(frozen.get("content_version")) == str(skill.content_version)
                and load.task_id == materialize.task_id
                and load.finished_at <= materialize.created_at
            ):
                loaded.append(load)
        if not loaded:
            continue
        paths = {path, path.removeprefix("/workspace/")}
        for execution in bound:
            command = execution.arguments.get("command")
            if (
                execution.tool_name != "execute"
                or execution.task_id != materialize.task_id
                or materialize.finished_at > execution.created_at
                or not isinstance(command, str)
                or not any(value and value in command for value in paths)
            ):
                continue
            related = sorted(
                (
                    event
                    for event in events
                    if event.run_id == run_id
                    and str(event.payload.get("scope_id")) == str(root_scope_id)
                    and str(event.payload.get("operation_id")) == str(execution.id)
                    and str(event.payload.get("tool_call_id")) == str(execution.tool_call_id)
                ),
                key=lambda event: event.seq,
            )
            for finished in related:
                exit_code = finished.payload.get("exit_code")
                if (
                    finished.event_type != "sandbox.finished"
                    or type(exit_code) is not int
                    or exit_code != 0
                ):
                    continue
                stdout = "".join(
                    event.payload.get("content", "")
                    for event in related
                    if event.event_type == "sandbox.log"
                    and event.payload.get("stream") == "stdout"
                    and event.seq < finished.seq
                )
                for line in stdout.splitlines():
                    try:
                        output = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    count = output.get("verified_scenes") if isinstance(output, dict) else None
                    if type(count) is int and count == 42:
                        return {
                            "run_id": str(run_id),
                            "scope_id": str(root_scope_id),
                            "skill_id": str(skill.id),
                            "activation_id": activation_id,
                            "materialize_operation_id": str(materialize.id),
                            "execute_operation_id": str(execution.id),
                            "tool_call_id": str(execution.tool_call_id),
                            "path": path,
                            "checksum": checksum,
                            "command": command,
                            "verified_scenes": count,
                            "exit_code": exit_code,
                        }
    raise AssertionError("missing_frozen_chain_script_witness")


@pytest.mark.parametrize("repetition", range(3))
def test_live_long_creation_with_two_real_summaries_and_dependency_chain(live_case, repetition):
    case = live_case
    with case.factory() as session:
        session.info["actor"] = task_actor()
        version = session.get(AIModelConfig, case.model_id).row_version
        session.rollback()
        AgentModelService(session, case.settings).patch_inputs(
            case.model_id,
            {
                "row_version": str(version),
                "image": True,
                "audio": False,
                "context_window_tokens": 65536,
            },
        )
    scenes = long_materials()
    skills = []
    for index in range(2):
        resources = [
            {"path": f"references/scene-{number + 1:02d}.md", "content": scene["content"]}
            for number, scene in enumerate(scenes)
            if number // 21 == index
        ]
        if index == 1:
            resources.append(
                {
                    "path": "scripts/check_chain.py",
                    "kind": "script",
                    "content": "import json,sys\n"
                    "rows=json.load(open(sys.argv[1],encoding='utf-8'))\n"
                    "assert len(rows)==42\nprevious='START'\n"
                    "for row in rows:\n assert '前幕ID='+previous in row['script']\n"
                    " previous=str(row['id'])\n"
                    "print(json.dumps({'verified_scenes':len(rows)},ensure_ascii=False))\n",
                }
            )
        skills.append(
            case.skill(
                f"雨夜排演资料{index + 1}",
                f"LIVE_METHOD_BODY_{index}: 为雨夜寻亲短剧编写可追溯的排演卡。"
                f"本方法提供第{index * 21 + 1}至{(index + 1) * 21}幕实际资料。"
                "每幕必须从对应L3资料取得证据；五项证据逐字保留，24项调度要求以连贯场景呈现。"
                "每幕首次保存时必须写前幕已保存ID，不能用临时引用、猜ID或事后补写。"
                "实际读回所有成果，第二份方法的check_chain脚本须物化到隔离工作区后执行，"
                "把真实ID和读回剧本整理为JSON交给脚本核验。不要生成任何媒体。",
                resources,
            )
        )
    _conversation, accepted, project_id = case.create(
        "请由主助手亲自把两份雨夜排演资料合成为42幕连续短剧，实际保存到本作品。"
        "分场创作与保存、最终链条脚本执行由主助手亲自完成；辅助只读核验和JSON汇集可按需委派子任务，子任务不能修改工作台业务数据。"
        "每幕至少1000字，完整呈现本幕调度资料，保留五项证据，人物误会不能有超自然解释。"
        "每幕首版的剧本须写前一幕已经保存的真实ID，第一幕为前幕ID=START；"
        "因此下一幕依赖上一幕真实存在，不能先批量创建空幕、用临时ID或事后回填。"
        "两份方法均适用，请自行发现使用；按需获取L3，不把口头计划当成果。"
        "最后用附带脚本核验真实读回的42幕链条，交付实际保存与校验结果。"
        "质量审计发现未通过项时，仍完成可执行的独立链条校验，最后如实报告问题。",
        skills=skills,
    )
    case.execute(accepted)
    assert_success(case, accepted)
    operations = case.operations(accepted)
    with case.factory() as session:
        root_scope = session.scalars(
            select(AgentExecutionScope).where(
                AgentExecutionScope.run_id == accepted.run.id,
                AgentExecutionScope.parent_scope_id.is_(None),
            )
        ).one()
        root_scope_id = root_scope.id
    root_operations = [item for item in operations if item.scope_id == root_scope_id]
    successful = [item for item in root_operations if item.status == "succeeded"]
    loads = [item for item in successful if item.tool_name == "runtime__load_skill"]
    assert len({item.result["activation_id"] for item in loads}) == 2
    assert len(successful) >= 80
    assert any(item.tool_name == "runtime__materialize_skill_resource" for item in successful)
    assert any(item.tool_name == "execute" and "42" in str(item.result) for item in successful)
    assert not any(item.tool_name == "workbench__submit_generation_batch" for item in operations)
    # 沙箱资源IO也标为write，但只用于隔离工作区和受管资源交接。
    sandbox_resource_tools = {
        "runtime__import_resources",
        "runtime__export_artifacts",
        "runtime__materialize_skill_resource",
    }
    child_business_writes = [
        item
        for item in operations
        if item.scope_id != root_scope_id
        and item.tool_name.startswith(("runtime__", "workbench__"))
        and item.tool_name not in sandbox_resource_tools
        and get_spec(item.tool_name).effect in {"write", "submit"}
    ]
    assert not child_business_writes
    creations = [
        change
        for item in successful
        for change in item.changes
        if change["kind"] == "shot" and change["before"] is None
    ]
    assert len(creations) == 42
    previous = "START"
    for scene, change in zip(scenes, creations, strict=True):
        script = change["after"]["script"]
        assert len(script) >= 1000 and f"前幕ID={previous}" in script
        assert all(value in script for value in scene["mandatory"])
        previous = change["id"]
    with case.factory() as session:
        previous = "START"
        for scene, change in zip(scenes, creations, strict=True):
            saved = session.get(ShotScript, int(change["id"]))
            assert saved is not None and saved.project_id == project_id
            assert saved.title.strip() and saved.title == change["after"]["title"]
            assert len(saved.script) >= 1000 and f"前幕ID={previous}" in saved.script
            assert all(value in saved.script for value in scene["mandatory"])
            assert saved.script == change["after"]["script"]
            previous = str(saved.id)
        tool_calls = session.scalars(
            select(AgentToolCall).where(AgentToolCall.run_id == accepted.run.id)
        ).all()
        sandbox_events = session.scalars(
            select(AgentEvent)
            .where(
                AgentEvent.run_id == accepted.run.id,
                AgentEvent.event_type.in_({"sandbox.log", "sandbox.finished"}),
            )
            .order_by(AgentEvent.seq)
        ).all()
        chain_witness = assert_long_chain_script_witness(
            operations,
            tool_calls,
            sandbox_events,
            run_id=accepted.run.id,
            root_scope_id=root_scope_id,
            skill=skills[1],
            script=next(
                item["content"] for item in resources if item["path"] == "scripts/check_chain.py"
            ),
        )
        turns = session.scalars(
            select(AgentTurn)
            .where(AgentTurn.run_id == accepted.run.id, AgentTurn.scope_id == root_scope_id)
            .order_by(AgentTurn.id)
        ).all()
        counts = Counter(turn.usage.get("source") for turn in turns)
        assert counts["assistant"] >= 40 and counts["summarization"] >= 2
        compactions = [
            turn
            for turn in turns
            if turn.usage.get("source") == "assistant"
            and (turn.response or {}).get("graph_compaction")
        ]
        assert len(compactions) >= 2
        case.facts["independent_context_compactions"] = len(compactions)
        case.facts["summary_helper_calls"] = counts["summarization"]
        first = next(turn for turn in turns if turn.usage.get("source") == "assistant")
        assert "LIVE_METHOD_BODY_" not in json.dumps(first.request_messages, ensure_ascii=False)
        assert (
            session.scalar(
                select(func.count())
                .select_from(ShotScript)
                .where(ShotScript.project_id == project_id)
            )
            == 42
        )
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
    case.facts.update(
        root_scope_id=str(root_scope_id),
        root_assistant_calls=counts["assistant"],
        root_summary_helper_calls=counts["summarization"],
        root_successful_tool_calls=len(successful),
        root_skill_activations=len({item.result["activation_id"] for item in loads}),
        root_context_compactions=len(compactions),
        root_saved_scenes=len(creations),
        root_chain_script_executed=bool(chain_witness),
        root_chain_script_witness=chain_witness,
        child_business_write_count=len(child_business_writes),
        auxiliary_tasks=sum(item.tool_name == "task" for item in operations),
        saved_scenes=42,
        persisted_scene_readback=True,
        first_version_dependency_chain=True,
        media_external_requests=0,
    )
    case.passed = True


@pytest.mark.parametrize("repetition", range(2))
@pytest.mark.parametrize("outcome", ["answered", "skipped"])
@pytest.mark.parametrize("child", [False, True])
def test_live_question_api_and_cross_worker_resume(live_case, repetition, outcome, child):
    case = live_case
    _conversation, accepted, project_id = case.create(
        (
            "请委派一个独立子任务负责向我确认结尾，取得回答后由该子任务实际保存一幕结尾。"
            if child
            else "请先向我确认结尾，取得回答后实际保存一幕结尾。"
        )
        + "结尾尚未确定，不能自行决定；提供可跳过的问题卡。若我跳过，保存题为‘待确认’的开放结尾。"
    )
    assert case.execute(accepted).status == "waiting_input"
    pending = questions(case, accepted)
    assert len(pending) == 1 and pending[0].wait_state["interrupt_id"]
    operation = pending[0]
    payload = question_answer(operation, outcome=outcome)
    path = f"/assistant/operations/{operation.id}/answer"
    with case.question_api() as client:
        invalid = client.post(
            path, json={**payload, "question_version": "2"}, headers={"idempotency-key": "stale"}
        )
        assert invalid.status_code == 409
        first = client.post(path, json=payload, headers={"idempotency-key": "answer"})
        assert first.status_code == 200, first.text
        assert (
            client.post(path, json=payload, headers={"idempotency-key": "duplicate"}).json()
            == first.json()
        )
    # The original worker is gone; the answer is read from durable wait state by a new instance.
    case.execute(accepted)
    assert_success(case, accepted)
    with case.factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentMessage)
                .where(AgentMessage.kind == "tool_answer")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ShotScript)
                .where(ShotScript.project_id == project_id)
            )
            == 1
        )
    with case.factory() as session:
        state = task_service(session, case.settings).task_runtime_state(
            accepted.task.conversation_id
        )
        assert any(
            item.operation_id == operation.id and not item.can_answer
            for item in state.pending_questions
        )
        scope = session.get(AgentExecutionScope, operation.scope_id)
        assert (scope.parent_scope_id is not None) == child
    case.facts.update(
        answer_outcome=outcome, question_scope="child" if child else "root", cross_worker=True
    )
    case.passed = True


@pytest.mark.parametrize("repetition", range(2))
def test_live_three_parallel_children_save_isolated_results(live_case, repetition):
    case = live_case
    _conversation, accepted, project_id = case.create(
        "请同时委派三条互不依赖的支线写作，每条由独立子任务负责："
        "钟楼寻人、寄存处误会、站台认亲。每条各实际保存一幕，主助手读回三幕后汇总。"
        "每条只写自己的场景，不读取或修改另一条；不生成媒体。"
    )
    case.execute(accepted)
    assert_success(case, accepted)
    with case.factory() as session:
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == accepted.run.id)
        ).all()
        children = [scope for scope in scopes if scope.parent_scope_id is not None]
        assert len(children) == 3
        calls = session.scalars(
            select(AgentToolCall).where(
                AgentToolCall.run_id == accepted.run.id, AgentToolCall.tool_name == "task"
            )
        ).all()
        assert len(calls) == 3 and len({call.turn_id for call in calls}) == 1
        for scope in children:
            saved = session.scalars(
                select(AgentOperation).where(
                    AgentOperation.scope_id == scope.id,
                    AgentOperation.tool_name == "workbench__apply_standard_patch",
                    AgentOperation.status == "succeeded",
                )
            ).all()
            assert (
                len(
                    [
                        change
                        for item in saved
                        for change in item.changes
                        if change["kind"] == "shot" and change["before"] is None
                    ]
                )
                == 1
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ShotScript)
                .where(ShotScript.project_id == project_id)
            )
            == 3
        )
    case.facts.update(
        three_parallel_child_scopes=True, shared_parent_budget=True, media_external_requests=0
    )
    case.passed = True


def media_scene(case):
    with case.factory() as session:
        task_service(session, case.settings)
        from short_drama.service.projects.management.project import ProjectService

        project = ProjectService(session).create_project(
            {"name": "live受控媒体", "workspace_mode": "standard"},
            idempotency_key=uuid4().hex,
        )
        assets = [
            AssetService(session).create({"kind": "character", "name": name}, project_id=project.id)
            for name in ("林晚", "乘客")
        ]
        model = AIModelConfigService(session).create(
            {
                "service_type": "image",
                "name": "Controlled media only",
                "model_key": "fixture",
                "provider": "ark",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            }
        )
    references = [
        {
            "project_id": str(project.id),
            "source_kind": "project",
            "source_id": str(project.id),
            "kind": "asset",
            "id": str(asset.id),
            "revision": str(asset.row_version),
        }
        for asset in assets
    ]
    return project.id, references, model


def settle_media(case, accepted, references):
    submitted = [
        item
        for item in case.operations(accepted)
        if item.tool_name == "workbench__submit_generation_batch" and item.status == "succeeded"
    ]
    calls = 0
    for operation in submitted:
        flow = SimpleNamespace(
            factory=case.factory,
            settings=case.settings,
            reference=references[0],
            operation_id=operation.id,
        )
        provider, worker = media_worker(flow, storage=case.objects)
        drain_assistant_media(flow, worker)
        calls += len(provider.calls)
    from short_drama.agent.coordinator.media_runtime import collect_media_results

    collect_media_results(case.factory, case.settings)
    return calls


@pytest.mark.parametrize("repetition", range(2))
def test_live_native_async_wait_rebuilds_worker_without_repeating_business(live_case, repetition):
    case = live_case
    project_id, references, _model = media_scene(case)
    _conversation, accepted, _ = case.create(
        "请为已选的林晚和乘客各生成一张肖像，使用本人目录里的受控图片模型。"
        "受理后在图片生成期间先实际创建一个名叫‘站长’的角色，完成这项独立工作后再明确等待两个图片任务。"
        "图片全部落库后核对真实媒体身份并总结，不要把受理当成完成。",
        references=references,
        project_id=project_id,
        media=True,
    )
    assert case.execute(accepted).status == "waiting_generation"
    operations = case.operations(accepted)
    waits = [item for item in operations if item.tool_name == "runtime__wait_generation"]
    assert waits and all(item.wait_state["interrupt_id"] for item in waits)
    with case.factory() as session:
        native_ids = set(session.scalars(select(AsyncTask.id)))
        assert len(native_ids) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(Asset)
                .where(Asset.project_id == project_id, Asset.name == "站长")
            )
            == 1
        )
        receipts = session.scalar(select(func.count()).select_from(BusinessToolInvocation))
    assert settle_media(case, accepted, references) == 2
    case.execute(accepted)
    assert_success(case, accepted)
    with case.factory() as session:
        assert set(session.scalars(select(AsyncTask.id))) == native_ids
        assert all(
            session.get(AsyncTask, identifier).status == "succeeded" for identifier in native_ids
        )
        assert all(
            item.wait_state["consumed"]
            for item in session.scalars(
                select(AgentOperation).where(AgentOperation.tool_name == "runtime__wait_generation")
            )
        )
        assert session.scalar(select(func.count()).select_from(BusinessToolInvocation)) >= receipts
    case.facts.update(
        cross_worker=True,
        controlled_media_submissions=2,
        media_external_requests=0,
        native_tasks_unchanged=True,
    )
    case.passed = True


@pytest.mark.parametrize("repetition", range(2))
@pytest.mark.parametrize("boundary", ["stream", "tool", "code", "media", "question"])
def test_live_stop_boundaries_and_new_message_use_a_new_run(live_case, repetition, boundary):
    case = live_case
    references = []
    project_id = None
    if boundary == "media":
        project_id, references, _model = media_scene(case)
    goal = {
        "stream": "直接写一篇1500字的雨夜场景分析，不调用工具。",
        "tool": "实际创建一个名叫‘停止前角色’的人物，再读回人物目录并总结。",
        "code": "用隔离代码工具执行Python，先输出准备就绪再等待60秒，结束后核对作品目录。",
        "media": "为已选林晚和乘客各受理一张肖像，使用受控图片模型；随后明确等待两张图片落库。",
        "question": "先问我结尾是否团圆，必须等我的答案后才能保存一幕结尾。",
    }[boundary]
    conversation, accepted, project_id = case.create(
        goal, project_id=project_id, references=references, media=boundary == "media"
    )
    result = case.execute(
        accepted, fault="stop_" + boundary if boundary in {"stream", "tool", "code"} else None
    )
    if boundary in {"media", "question"}:
        assert result.status == ("waiting_generation" if boundary == "media" else "waiting_input")
        case.stop(accepted)
    else:
        assert case.facts["worker_deliveries"][-1]["boundary"] == "stop_" + boundary
    with case.factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "cancelled"
        assert session.get(AgentTask, accepted.task.id).status == "stopped"
        before_calls = run.usage["decision_calls"]
        before_turns = set(session.scalars(select(AgentTurn.id).where(AgentTurn.run_id == run.id)))
        before_operations = {item.id for item in case.operations(accepted)}
        before_assets = session.scalar(select(func.count()).select_from(Asset))
    if boundary == "media":
        assert settle_media(case, accepted, references) == 2
    if boundary == "question":
        operation = questions(case, accepted)[0]
        with case.question_api() as client:
            response = client.post(
                f"/assistant/operations/{operation.id}/answer",
                json=question_answer(operation),
                headers={"idempotency-key": "late-stopped-answer"},
            )
        assert response.status_code == 409
    case.execute(accepted)
    with case.factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "cancelled" and run.usage["decision_calls"] == before_calls
        assert (
            set(session.scalars(select(AgentTurn.id).where(AgentTurn.run_id == run.id)))
            == before_turns
        )
        assert {item.id for item in case.operations(accepted)} == before_operations
    resumed = case.followup(
        accepted, "停止后请核对这项任务已经结算的实际成果并简述；不创建、不修改，也不生成新媒体。"
    )
    assert resumed.task.id == accepted.task.id and resumed.run.id != accepted.run.id
    case.execute(resumed)
    assert_success(case, resumed)
    with case.factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "cancelled"
        assert session.scalar(select(func.count()).select_from(Asset)) == before_assets
        if boundary == "tool":
            assert before_assets == 1
            assert any(
                item.tool_name
                in {
                    "runtime__read_task_results",
                    "runtime__read_operation_receipt",
                    "runtime__describe_creation_target",
                }
                for item in case.operations(resumed)
            )
    trace_roundtrip(case, conversation, resumed)
    case.facts.update(
        stop_boundary=boundary,
        stopped_run_never_revived=True,
        new_message_new_run=True,
        media_external_requests=0,
    )
    case.passed = True


@pytest.mark.parametrize("repetition", range(2))
@pytest.mark.parametrize(
    "boundary", ["business_committed", "interrupt_registered", "answer_committed"]
)
def test_live_crash_windows_keep_one_business_or_answer_identity(live_case, repetition, boundary):
    case = live_case
    goal = (
        "实际创建且只创建一个名叫‘崩溃恢复角色’的人物，读回后简述。"
        if boundary == "business_committed"
        else "结尾尚未确定，先问我是否团圆，收到答案后实际保存且只保存一幕‘恢复后的结尾’。"
    )
    _conversation, accepted, project_id = case.create(goal)
    if boundary == "business_committed":
        result = case.execute(accepted, fault=boundary)
        assert result.status == "running"
        receipts = invocation_ids(case, accepted)
        assert len(receipts) == 1
        expire(case, accepted)
    else:
        if boundary == "interrupt_registered":
            assert case.execute(accepted, fault=boundary).status == "running"
            first_question = questions(case, accepted)
            assert len(first_question) == 1 and not first_question[0].wait_state.get("interrupt_id")
            expire(case, accepted)
        assert case.execute(accepted).status == "waiting_input"
        operation = questions(case, accepted)[0]
        assert len(questions(case, accepted)) == 1 and operation.wait_state["interrupt_id"]
        payload = question_answer(operation)
        if boundary == "answer_committed":
            case.execute(
                accepted,
                fault=boundary,
                answer={"operation_id": str(operation.id), "payload": payload},
            )
            assert case.facts["worker_deliveries"][-1]["answer_http_status"] == 500
        else:
            answer_question(case, operation)
        with case.question_api() as client:
            retry = client.post(
                f"/assistant/operations/{operation.id}/answer",
                json=payload,
                headers={"idempotency-key": "after-crash-retry"},
            )
        assert retry.status_code == 200
        receipts = invocation_ids(case, accepted)
    case.execute(accepted)
    assert_success(case, accepted)
    assert receipts <= invocation_ids(case, accepted)
    assert any(item.get("crash_injected") for item in case.facts["worker_deliveries"])
    with case.factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 1
        if boundary == "business_committed":
            assert session.scalar(select(func.count()).select_from(Asset)) == 1
            assert invocation_ids(case, accepted) == receipts
        else:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentMessage)
                    .where(AgentMessage.kind == "tool_answer")
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ShotScript)
                    .where(ShotScript.project_id == project_id)
                )
                == 1
            )
            assert len(questions(case, accepted)) == 1
    case.facts.update(
        crash_boundary=boundary,
        duplicate_business_writes=0,
        duplicate_questions=0,
        duplicate_answers=0,
        cross_worker=True,
    )
    case.passed = True


@pytest.mark.parametrize("repetition", range(2))
def test_live_two_waiting_children_resume_by_scope_and_keep_completed_sibling(
    live_case, repetition
):
    case = live_case
    _conversation, accepted, project_id = case.create(
        "请同时委派三个独立子任务。第一条‘钟楼’必须先问我钟楼结局，收到回答后实际保存一幕；"
        "第二条‘寄存处’必须先问我寄存处结局，收到回答后实际保存一幕；"
        "第三条‘站台’不需提问，直接实际保存一幕并完成。父助手等待各自交付后总结，不代写、不重复委派。"
    )
    assert case.execute(accepted).status == "waiting_input"
    pending = questions(case, accepted)
    assert len(pending) == 2 and len({item.scope_id for item in pending}) == 2
    assert len({item.wait_state["interrupt_id"] for item in pending}) == 2
    with case.factory() as session:
        children = session.scalars(
            select(AgentExecutionScope).where(
                AgentExecutionScope.run_id == accepted.run.id,
                AgentExecutionScope.parent_scope_id.is_not(None),
            )
        ).all()
        assert len(children) == 3
        finished = next(
            item for item in children if item.id not in {row.scope_id for row in pending}
        )
        sibling_turns = set(
            session.scalars(select(AgentTurn.id).where(AgentTurn.scope_id == finished.id))
        )
        sibling_ops = set(
            session.scalars(select(AgentOperation.id).where(AgentOperation.scope_id == finished.id))
        )
        assert session.scalar(select(func.count()).select_from(ShotScript)) == 1
    answer_question(case, pending[0])
    assert case.execute(accepted).status == "waiting_input"
    with case.factory() as session:
        assert session.get(AgentOperation, pending[0].id).wait_state["consumed"]
        assert not session.get(AgentOperation, pending[1].id).wait_state["consumed"]
        assert (
            set(session.scalars(select(AgentTurn.id).where(AgentTurn.scope_id == finished.id)))
            == sibling_turns
        )
        assert (
            set(
                session.scalars(
                    select(AgentOperation.id).where(AgentOperation.scope_id == finished.id)
                )
            )
            == sibling_ops
        )
    answer_question(case, pending[1])
    case.execute(accepted)
    assert_success(case, accepted)
    with case.factory() as session:
        assert (
            set(session.scalars(select(AgentTurn.id).where(AgentTurn.scope_id == finished.id)))
            == sibling_turns
        )
        assert (
            set(
                session.scalars(
                    select(AgentOperation.id).where(AgentOperation.scope_id == finished.id)
                )
            )
            == sibling_ops
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ShotScript)
                .where(ShotScript.project_id == project_id)
            )
            == 3
        )
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 1
    case.facts.update(
        distinct_waiting_child_scopes=2, completed_sibling_replayed=False, cross_worker=True
    )
    case.passed = True


def canonical_current_activations(messages, message_id, skill_id):
    for message in reversed(messages):
        content = message.get("content")
        if message["role"] != "user" or not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except ValueError:
            continue
        if (
            isinstance(payload, dict)
            and "task_user_sources" in payload
            and "current_skill_activations" in payload
            and any(item["id"] == str(message_id) for item in payload["task_user_sources"]["items"])
        ):
            records = [
                item
                for item in payload["current_skill_activations"]
                if item["skill_id"] == str(skill_id)
            ]
            assert records, "canonical_context_skill_state_missing"
            return records
    raise AssertionError("canonical_context_current_projection_missing")


def assert_canonical_original_group(messages, original_call, original_group):
    rows = [
        row
        for row in messages
        if row["role"] == "assistant"
        and any(call["id"] == original_call.provider_call_id for call in row.get("tool_calls", []))
    ]
    assert len(rows) == 1, "canonical_context_original_call_missing_or_duplicated"
    assert [
        (call["id"], call["function"]["name"], json.loads(call["function"]["arguments"]))
        for call in rows[0]["tool_calls"]
    ] == [
        (call.provider_call_id, call.tool_name, call.arguments.get("parsed", call.arguments))
        for call in original_group
    ], "canonical_context_original_group_changed"
    expected = Counter(call.provider_call_id for call in original_group)
    assert (
        Counter(
            row["tool_call_id"]
            for row in messages
            if row["role"] == "tool" and row["tool_call_id"] in expected
        )
        == expected
    ), "canonical_context_original_group_results_missing_or_duplicated"


def _canonical_answered_question_view(
    messages, turn, turns, tool_calls, operations, original, markers
):
    from copy import deepcopy

    from short_drama.schemas.assistant.operations.questions import AskUserRequest

    view = deepcopy(messages)
    for row in view:
        for wire in row.get("tool_calls", []):
            arguments = json.loads(wire["function"]["arguments"])
            if wire["function"]["name"] != "runtime__ask_user" or not any(
                marker in json.dumps(arguments) for marker in markers
            ):
                continue
            stored = [item for item in tool_calls if item.provider_call_id == wire["id"]]
            assert len(stored) == 1, "canonical_question_call_identity_mismatch"
            call = stored[0]
            assert (
                call.run_id == turn.run_id == original.run_id
                and call.scope_id == turn.scope_id == original.scope_id
                and call.tool_name == "runtime__ask_user"
                and call.status == "succeeded"
            ), "canonical_question_call_identity_mismatch"
            source_turn = next(item for item in turns if item.id == call.turn_id)
            assert (
                source_turn.run_id == turn.run_id
                and source_turn.scope_id == turn.scope_id
                and source_turn.status == "succeeded"
                and source_turn.usage.get("source") == "assistant"
                and source_turn.turn_no < turn.turn_no
            ), "canonical_question_original_turn_mismatch"
            group = sorted(
                [item for item in tool_calls if item.turn_id == call.turn_id],
                key=lambda item: item.call_index,
            )
            assert all(
                item.run_id == call.run_id
                and item.scope_id == call.scope_id
                and item.tool_name
                not in {
                    "runtime__load_skill",
                    "runtime__read_skill_resource",
                    "runtime__release_skill",
                }
                and not (item.result or {}).get("source_context", {}).get("skill_activation_ids")
                and not (item.result or {})
                .get("message", {})
                .get("data", {})
                .get("additional_kwargs", {})
                .get("skill_activation_ids")
                for item in group
            ), "canonical_question_not_independent_group"
            assert_canonical_original_group(messages, call, group)
            assert arguments == call.arguments.get("parsed", call.arguments), (
                "canonical_question_original_arguments_changed"
            )
            matched = [item for item in operations if item.tool_call_id == call.id]
            assert len(matched) == 1, "canonical_question_operation_identity_mismatch"
            operation = matched[0]
            assert (
                operation.run_id == call.run_id
                and operation.scope_id == call.scope_id
                and operation.task_id == original.task_id
                and operation.tool_name == call.tool_name
                and operation.status == "succeeded"
                and operation.phase == "terminal"
                and operation.finished_at is not None
                and operation.error is None
            ), "canonical_question_operation_identity_mismatch"
            assert AskUserRequest.model_validate(arguments).model_dump(mode="json") == (
                operation.arguments
            ), "canonical_question_operation_arguments_changed"
            state = operation.wait_state or {}
            assert (
                state.get("kind") == "user_input"
                and state.get("scope_id") == str(call.scope_id)
                and state.get("tool_call_id") == str(call.id)
                and state.get("ready") is True
                and state.get("consumed") is True
                and state.get("answer_message_id")
                and state.get("answer", {}).get("outcome") == "answered"
                and state["answer"]["question_version"] == state["question_version"]
            ), "canonical_question_answer_not_consumed"
            answer_id = state["answer_message_id"]
            receipt = state.get("answer_receipt", {})
            assert (
                receipt.get("operation_id") == str(operation.id)
                and receipt.get("run_id") == str(operation.run_id)
                and receipt.get("answer_message_id") == answer_id
            ), "canonical_question_answer_identity_mismatch"
            expected = {
                **state["answer"],
                "answer_message_id": answer_id,
                "source_message_ids": [answer_id],
            }
            assert all(operation.result.get(key) == value for key, value in expected.items()), (
                "canonical_question_answer_result_mismatch"
            )
            assert operation.result["receipt_operation_id"] == str(operation.id)
            saved = call.result["message"]["data"]
            assert (
                saved["tool_call_id"] == call.provider_call_id
                and saved["name"] == call.tool_name
                and saved["additional_kwargs"]["source_message_ids"] == [answer_id]
                and saved["additional_kwargs"]["answer_message_id"] == answer_id
                and json.loads(saved["content"]) == expected
            ), "canonical_question_saved_answer_mismatch"
            result = next(item for item in messages if item.get("tool_call_id") == wire["id"])
            assert json.loads(result["content"]) == expected, (
                "canonical_question_wire_answer_mismatch"
            )
            assert any(
                item.tool_name == "runtime__release_skill"
                and item.status == "succeeded"
                and item.run_id == operation.run_id
                and item.scope_id == operation.scope_id
                and operation.finished_at <= item.created_at
                and item.finished_at <= turn.created_at
                for item in operations
            ), "canonical_question_not_before_release"
            # 只排除原表单的显示文本，普通AI内容、答案及非显示参数仍接受来源检查。
            for key in ("question", "title"):
                if key in arguments:
                    arguments[key] = ""
            for field in arguments["fields"]:
                for key in ("label", "placeholder"):
                    if key in field:
                        field[key] = ""
                for option in field.get("options") or []:
                    option["label"] = ""
            wire["function"]["arguments"] = json.dumps(arguments, ensure_ascii=False)
    return view


def assert_canonical_skill_requests(
    turns, tool_calls, loads, reads, skill, message_id, body_marker, resource_marker, operations
):
    original = loads[0]
    canonical_id = original.result["body_operation_id"]
    calls_by_id = {item.id: item for item in tool_calls}
    original_call = calls_by_id[original.tool_call_id]
    assert original_call.tool_name == "runtime__load_skill"
    assert original_call.scope_id == original.scope_id
    original_group = sorted(
        [
            item
            for item in tool_calls
            if item.scope_id == original.scope_id and item.turn_id == original_call.turn_id
        ],
        key=lambda item: item.call_index,
    )
    original_turn = next(item for item in turns if item.id == original_call.turn_id)
    reloaded_ids = {
        item.result["activation_id"]
        for item in loads
        if item.scope_id == original.scope_id
        and item.result["activation_id"] != original.result["activation_id"]
        and item.result["body_operation_id"] == canonical_id
        and item.result["content_version"] == str(skill.content_version)
    }
    assert reloaded_ids, "canonical_context_no_frozen_reactivation"
    post_reads = {
        str(item.id): item
        for item in reads
        if item.scope_id == original.scope_id
        and str(item.arguments["activation_id"]) in reloaded_ids
        and item.arguments["path"] == "references/evidence.md"
        and item.result["activation_id"] == str(item.arguments["activation_id"])
    }
    assert post_reads, "canonical_context_no_post_reload_read"
    assistant_turns = sorted(
        [
            item
            for item in turns
            if item.scope_id == original.scope_id
            and item.status == "succeeded"
            and item.usage.get("source") == "assistant"
        ],
        key=lambda item: item.turn_no,
    )
    assert assistant_turns, "canonical_context_no_assistant_request"
    assert body_marker not in json.dumps(assistant_turns[0].request_messages)
    activation_ids = {item.result["activation_id"] for item in loads}
    witnesses, final_active = [], None
    for turn in assistant_turns:
        if turn.turn_no <= original_turn.turn_no:
            continue
        assert len(turn.request_messages) == 1
        request = turn.request_messages[0]
        assert request["source"] == "assistant"
        messages = request["body"]["messages"]
        records = canonical_current_activations(messages, message_id, skill.id)
        active = {item["activation_id"] for item in records if item["active"] is True}
        assert all(
            item["content_version"] == str(skill.content_version)
            and item["body_operation_id"] == canonical_id
            for item in records
            if item["active"] is True
        ), "canonical_context_active_version_changed"
        wire_calls = [call for row in messages for call in row.get("tool_calls", [])]
        results = [row for row in messages if row["role"] == "tool"]
        assert Counter(item["id"] for item in wire_calls) == Counter(
            item["tool_call_id"] for item in results
        ), "canonical_context_incomplete_tool_groups"
        decoded = [(row, json.loads(row["content"])) for row in results]
        bodies = [
            (row, value)
            for row, value in decoded
            if value.get("body_operation_id") == canonical_id and "instructions" in value
        ]
        assert (
            len(bodies) <= 1
            and sum(body_marker in value.get("instructions", "") for _row, value in decoded) <= 1
        ), "canonical_context_duplicate_body"
        final_active = bool(active)
        if not active:
            assert all(item["active"] is False for item in records)
            encoded = json.dumps(
                _canonical_answered_question_view(
                    messages,
                    turn,
                    turns,
                    tool_calls,
                    operations,
                    original,
                    (body_marker, resource_marker),
                )
            )
            assert body_marker not in encoded and resource_marker not in encoded, (
                "canonical_context_released_source_returned"
            )
            assert not bodies
            assert not any(
                value.get("activation_id") in activation_ids and value.get("content")
                for _row, value in decoded
            ), "canonical_context_released_resource_returned"
            continue
        assert len(bodies) == 1, "canonical_context_active_body_missing"
        body_row, body = bodies[0]
        assert body["instructions"] == original.result["instructions"]
        assert body_row["tool_call_id"] == original_call.provider_call_id
        assert_canonical_original_group(messages, original_call, original_group)
        for row, value in decoded:
            read = post_reads.get(value.get("receipt_operation_id"))
            if read is None or value.get("activation_id") not in active:
                continue
            stored = calls_by_id[read.tool_call_id]
            matching = [item for item in wire_calls if item["id"] == row["tool_call_id"]]
            assert len(matching) == 1
            assert row["tool_call_id"] == stored.provider_call_id
            assert (
                stored.tool_name
                == matching[0]["function"]["name"]
                == ("runtime__read_skill_resource")
            )
            assert json.loads(matching[0]["function"]["arguments"]) == stored.arguments.get(
                "parsed", stored.arguments
            )
            assert value["activation_id"] == str(read.arguments["activation_id"])
            assert value["content"] == read.result["content"]
            assert resource_marker in value["content"]
            witnesses.append(
                {
                    "turn_id": str(turn.id),
                    "model_call_id": turn.usage["model_call_id"],
                    "scope_id": str(turn.scope_id),
                    "activation_id": value["activation_id"],
                    "body_operation_id": canonical_id,
                    "read_operation_id": str(read.id),
                }
            )
    assert witnesses, "canonical_context_no_active_reread_witness"
    assert final_active is not None, "canonical_context_final_assistant_state_missing"
    return {"witness": witnesses[0], "final_active": final_active}


@pytest.mark.parametrize("repetition", range(2))
def test_live_skill_canonical_reload_frozen_directory_and_trace(live_case, repetition):
    case = live_case
    body_marker, resource_marker = "BODY_" + uuid4().hex, "RESOURCE_" + uuid4().hex
    skill = case.skill(
        "雨夜取证方法",
        body_marker + "。逐字核对资料证据，保留动作因果，不生成媒体。",
        [
            {
                "path": "references/evidence.md",
                "content": resource_marker + "\n人物先听钟，再见钥匙。",
            }
        ],
        description="雨夜取证方法：" + "先检查动作因果与证据来源。" * 100 + "尾部唯一检索词",
        exclusions="不适用于真实媒体生成。" * 90,
    )
    conversation, accepted, _ = case.create(
        "请检索‘尾部唯一检索词’，核对雨夜取证方法的适用范围和不适用范围。"
        "先加载并读其evidence资料；保持当前激活，不先释放，再加载该方法的同一版本一次，"
        "核对第二次加载复用原激活且没有第二份正文；"
        "随后提问让我确认是否继续并等待。收到答案后释放方法再重载，重新读同一资料，"
        "比较重载前后的方法和证据是否仍为本次冻结版本，最后简述真实读回结果，不保存业务或生成媒体。"
    )
    assert case.execute(accepted).status == "waiting_input"
    initial = case.operations(accepted)
    loads = [
        item
        for item in initial
        if item.tool_name == "runtime__load_skill" and item.status == "succeeded"
    ]
    assert len(loads) >= 2, "canonical_duplicate_load_missing"
    assert loads[0].result["instructions"].startswith(body_marker)
    assert loads[1].result["activation_id"] == loads[0].result["activation_id"]
    assert "instructions" not in loads[1].result
    searches = [
        item
        for item in initial
        if item.tool_name == "runtime__search_skills" and item.status == "succeeded"
    ]
    found = next(
        item
        for operation in searches
        for item in operation.result["items"]
        if item["id"] == str(skill.id)
    )
    assert found["truncated"] and "尾部唯一检索词" in found["match_excerpt"]
    assert len(found["description"]) <= 800 and len(found["exclusions"]) <= 800
    with case.factory() as session:
        task_service(session, case.settings)
        AgentSkillService(session, case.settings, case.objects).patch(
            skill.id,
            {
                "row_version": str(skill.row_version),
                **(
                    {"instructions": "NEW_DIRECTORY_BODY"}
                    if repetition == 0
                    else {"enabled": False}
                ),
            },
        )
    answer_question(case, questions(case, accepted)[0])
    case.execute(accepted)
    assert_success(case, accepted)
    operations = case.operations(accepted)
    assert any(
        item.tool_name == "runtime__release_skill" and item.status == "succeeded"
        for item in operations
    )
    reloads = [
        item
        for item in operations
        if item.tool_name == "runtime__load_skill" and item.status == "succeeded"
    ]
    assert len(reloads) >= 3
    assert reloads[-1].result["content_version"] == str(skill.content_version)
    assert reloads[-1].result["activation_id"] != reloads[0].result["activation_id"]
    assert reloads[-1].result["body_operation_id"] == reloads[0].result["body_operation_id"]
    assert [item.id for item in reloads if "instructions" in item.result] == [reloads[0].id]
    reads = [
        item
        for item in operations
        if item.tool_name == "runtime__read_skill_resource" and item.status == "succeeded"
    ]
    assert len(reads) >= 2 and all(resource_marker in item.result["content"] for item in reads)
    with case.factory() as session:
        turns = session.scalars(
            select(AgentTurn).where(AgentTurn.run_id == accepted.run.id).order_by(AgentTurn.id)
        ).all()
        assert body_marker not in json.dumps(turns[0].request_messages)
        assert "NEW_DIRECTORY_BODY" not in json.dumps([turn.request_messages for turn in turns])
        tool_calls = session.scalars(
            select(AgentToolCall).where(AgentToolCall.run_id == accepted.run.id)
        ).all()
        context = assert_canonical_skill_requests(
            turns,
            tool_calls,
            reloads,
            reads,
            skill,
            accepted.message.id,
            body_marker,
            resource_marker,
            operations,
        )
    page = trace_roundtrip(case, conversation, accepted)
    skill_blocks = [item for item in page["items"] if item["target"]["kind"] == "skill"]
    assert skill_blocks and all(
        item["target"]["version"] == str(skill.content_version) for item in skill_blocks
    )
    assert any(item["target"]["display_name"] == skill.name for item in skill_blocks)
    case.facts.update(
        canonical_reload=True,
        canonical_context=context,
        frozen_directory_change="edit" if repetition == 0 else "disable",
        metadata_only_first_request=True,
        real_l3_reads=True,
    )
    case.passed = True


@pytest.mark.parametrize("repetition", range(2))
@pytest.mark.parametrize("withdrawal", ["selection", "forget"])
def test_live_skill_withdrawal_removes_body_resources_and_notes(live_case, repetition, withdrawal):
    from short_drama.service.assistant.memory.memory import AgentMemoryService

    case = live_case
    marker = "WITHDRAWN_" + uuid4().hex
    skill = case.skill(
        "撤回取证方法",
        marker + "；只用已读证据，不生成媒体。",
        [{"path": "references/source.md", "content": marker + "证据原句"}],
    )
    _conversation, accepted, _ = case.create(
        "请用已选取证方法读取source资料，依据真实读取操作记一条工作笔记；"
        "然后问我是否继续并等待。回答后只核对仍可用的材料，不恢复已撤回资料，最后简述。",
        skills=[skill],
    )
    assert case.execute(accepted).status == "waiting_input"
    assert any(
        item.tool_name == "runtime__record_task_observation" and item.status == "succeeded"
        for item in case.operations(accepted)
    )
    with case.factory() as session:
        before = set(session.scalars(select(AgentTurn.id)))
    if withdrawal == "selection":
        case.followup(accepted, "继续核对，不再使用先前方法。", skills=[])
    else:
        with case.factory() as session:
            task_service(session, case.settings)
            from tests.integration.standard.assistant.test_assistant_memory import (
                create_sourced_memory,
            )

            manager = AgentMemoryService(session)
            item = create_sourced_memory(
                manager,
                {
                    "kind": "preference",
                    "memory_key": "本次来源撤回",
                    "title": "本次来源撤回",
                    "content": "本次来源撤回",
                    "source_conversation_id": str(accepted.task.conversation_id),
                    "source_message_ids": [str(accepted.message.id)],
                },
            )
            manager.forget_item(item.id, {"content_version": str(item.content_version)})
        case.followup(accepted, "继续只核对当前允许资料；不要恢复已遗忘的来源。")
    answer_question(case, questions(case, accepted)[0])
    case.execute(accepted)
    assert_success(case, accepted)
    with case.factory() as session:
        turns = session.scalars(select(AgentTurn).where(AgentTurn.id.not_in(before))).all()
        assert turns and marker not in json.dumps([item.request_messages for item in turns])
        if withdrawal == "selection":
            task = session.get(AgentTask, accepted.task.id)
            assert task.selected_skills == []
    case.facts.update(
        withdrawal=withdrawal, withdrawn_body_resource_note_resurrected=False, cross_worker=True
    )
    case.passed = True


@pytest.mark.parametrize("repetition", range(2))
def test_live_parent_child_skill_activation_and_history_are_isolated(live_case, repetition):
    case = live_case
    parent_marker, child_marker = "PARENT_" + uuid4().hex, "CHILD_" + uuid4().hex
    parent = case.skill(
        "父级节奏方法", parent_marker + "。核对父级镜头节奏，标记仅用于核对，不在结论复述。"
    )
    child = case.skill(
        "子级对白方法", child_marker + "。核对子级对白节奏，标记仅用于核对，不在交付复述。"
    )
    _conversation, accepted, _ = case.create(
        "请主助手加载父级节奏方法并核对节奏。再委派一个独立子任务，"
        "让它自行发现并加载子级对白方法、给出一句对白节奏建议；"
        "不要把父方法正文或激活ID交给子任务，子任务交付只给建议，不引用正文标记。最后父级汇总。"
    )
    case.execute(accepted)
    assert_success(case, accepted)
    loads = [
        item
        for item in case.operations(accepted)
        if item.tool_name == "runtime__load_skill" and item.status == "succeeded"
    ]
    assert {item.result["skill_id"] for item in loads} == {str(parent.id), str(child.id)}
    assert len({item.scope_id for item in loads}) == 2
    assert len({item.result["activation_id"] for item in loads}) == 2
    with case.factory() as session:
        root = session.scalar(
            select(AgentExecutionScope).where(
                AgentExecutionScope.run_id == accepted.run.id,
                AgentExecutionScope.scope_key == "root",
            )
        )
        turns = session.scalars(select(AgentTurn).where(AgentTurn.run_id == accepted.run.id)).all()
        parents = json.dumps([item.request_messages for item in turns if item.scope_id == root.id])
        children = json.dumps([item.request_messages for item in turns if item.scope_id != root.id])
        assert parent_marker in parents and child_marker not in parents
        assert child_marker in children and parent_marker not in children
    case.facts.update(parent_child_skill_isolation=True, scoped_activation_identities=2)
    case.passed = True


@pytest.mark.parametrize("repetition", range(2))
def test_live_settings_freeze_and_realtime_media_permission(live_case, repetition):
    case = live_case
    project_id, references, _model = media_scene(case)
    with case.factory() as session:
        task_service(session, case.settings)
        service = AssistantSettingsService(session, case.settings)
        service.update_settings(
            {
                "settings_version": str(service.get_settings().settings_version),
                "custom_instructions": "SETTINGS_OLD_雨夜",
            }
        )
    conversation, accepted, _ = case.create(
        "先问我是否开始为两名已选角色各生成一张肖像并等待；回答后提交本人受控图片模型。"
        "如果实时设置阻断，解释实际原因，不改用其他入口，也不重试生成。",
        references=references,
        project_id=project_id,
        media=True,
    )
    assert case.execute(accepted).status == "waiting_input"
    with case.factory() as session:
        task_service(session, case.settings)
        original = dict(
            session.get(AgentRun, accepted.run.id).config_snapshot["assistant_settings"]
        )
        session.rollback()
        service = AssistantSettingsService(session, case.settings)
        service.update_settings(
            {
                "settings_version": str(service.get_settings().settings_version),
                "custom_instructions": "SETTINGS_NEW_钟楼",
            }
        )
    answer_question(case, questions(case, accepted)[0])
    case.execute(accepted, fault="close_policy_before_generation")
    assert_success(case, accepted)
    blocked = [
        item
        for item in case.operations(accepted)
        if item.tool_name == "workbench__submit_generation_batch"
    ]
    assert blocked and all(
        item.status == "failed" and item.error["code"] == "assistant_media_disabled"
        for item in blocked
    )
    with case.factory() as session:
        assert (
            session.get(AgentRun, accepted.run.id).config_snapshot["assistant_settings"] == original
        )
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0
    with case.factory() as session:
        task_service(session, case.settings)
        assert (
            not AssistantSettingsService(session, case.settings)
            .get_settings()
            .media_generation_enabled
        )
    _other_conversation, newer, _ = case.create("用当前指令简述一次镜头节奏，不生成媒体。")
    case.execute(newer)
    assert_success(case, newer)
    with case.factory() as session:
        run = session.get(AgentRun, newer.run.id)
        frozen = run.config_snapshot["assistant_settings"]
        assert frozen["instructions"] == "SETTINGS_NEW_钟楼"
        assert frozen["model_call_limit"] == case.settings.agent_run_decision_cap
        assert run.budget["output_tokens"] == case.settings.agent_run_output_token_cap
    trace_roundtrip(case, conversation, accepted)
    case.facts.update(
        old_run_settings_frozen=True,
        new_run_settings_refreshed=True,
        realtime_policy_blocked=True,
        media_external_requests=0,
    )
    case.passed = True
