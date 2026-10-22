"""Explicit free Agnes acceptance of V3 tools, memory and frozen task methods."""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import tests.integration.standard.assistant.test_global_assistant_tasks as task_tests
from short_drama.agent.memory.runtime import AgentMemoryRuntime
from short_drama.agent.gateway.protocol import AgentModelGateway
from short_drama.agent.coordinator.runtime import AgentRuntime
from short_drama.core.config import Settings
from short_drama.domain import Asset, ShotScript
from short_drama.domain.agent import AgentMessage, AgentRun
from short_drama.domain.agent_memory import AgentMemoryJob
from short_drama.domain.agent_task import AgentOperation, AgentTask
from short_drama.domain.canvas import CanvasNode
from short_drama.service.agent_memory_service import AgentMemoryService
from short_drama.service.agent_skill_service import AgentSkillService
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.asset_service import AssetService
from short_drama.service.canvas_service import CanvasService
from short_drama.service.project_service import ProjectService
from tests.integration.canvas.assistant.test_canvas_agnes_live import assert_no_secret, live_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_AGNES_LIVE") != "1",
        reason="Set RUN_AGNES_LIVE=1 for the explicitly authorized free Agnes model",
    ),
]


@pytest.fixture
def global_live_workspace(db_session, request):
    protocol = getattr(request, "param", "chat")
    settings = Settings(auth_enabled=True, agent_enabled=True)
    db_session.info["actor"] = task_tests.task_actor()
    model = AIModelConfigService(db_session).create(
        {
            "name": "V3 free Agnes acceptance",
            "service_type": "text",
            "provider": "Agnes",
            "model_key": "agnes-3.0-flash",
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": live_key(),
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": protocol},
        }
    )
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    return factory, settings, model.id


class _ObservedGateway(AgentModelGateway):
    def __init__(self, settings):
        super().__init__(settings)
        self.inputs, self.outputs = [], []

    async def run_segment(self, snapshot, credential, **kwargs):
        # Never save the snapshot or credential. All calls use the actual gateway and transport.
        self.inputs.append(
            {
                key: value
                for key, value in kwargs.items()
                if key in {"instructions", "user_prompt", "message_history", "deferred_results"}
            }
        )
        result = await super().run_segment(snapshot, credential, **kwargs)
        self.outputs.append({"protocol": result.protocol, "usage": result.usage})
        return result


def _finish(runtime, run_id, *, maximum=12):
    for _index in range(maximum):
        with runtime.factory() as session:
            run = session.get(AgentRun, run_id)
            if run.status == "succeeded":
                return
            assert run.status in {"queued", "running"}, run.error
            version = run.message_version
        runtime.execute_one(run_id, version)
    pytest.fail("The real V3 task did not finish within the bounded decision count")


def _write_report(directory: Path, gateway, **facts):
    report = {
        "model": "agnes-3.0-flash",
        "scope": "real V3 AgentRuntime and AgentModelGateway; disposable MySQL",
        "submissions": len(gateway.outputs),
        "segments": gateway.outputs,
        **facts,
    }
    assert_no_secret(report, live_key())
    (directory / "v3-agnes-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.mark.parametrize("global_live_workspace", ["chat", "responses"], indirect=True)
@pytest.mark.parametrize("mode", ["standard", "infinite_canvas"])
def test_global_assistant_agnes_creates_real_structures_without_implicit_body_or_media(
    global_live_workspace, tmp_path, mode
):
    factory, settings, model = global_live_workspace
    with factory() as session:
        service = task_tests.task_service(session, settings)
        project = ProjectService(session).create_project(
            {"name": "V3 live acceptance", "workspace_mode": mode}, idempotency_key="live-target"
        )
        target = {"mode": mode, "project_id": str(project.id)}
        if mode == "infinite_canvas":
            saved = CanvasService(session).read(project.id, project.primary_canvas_id, private=True)
            target["canvas_id"] = saved["source_document"]["id"]
            document = saved["source_document"]
            document["nodes"] = [
                {
                    "id": "private-unselected",
                    "type": "text",
                    "title": "未选入旧节点",
                    "position": {"x": -600, "y": -600},
                    "width": 350,
                    "height": 350,
                    "metadata": {
                        "content": "PRIVATE_UNSELECTED_BODY",
                        "prompt": "PRIVATE_UNSELECTED_PROMPT",
                    },
                }
            ]
            CanvasService(session).commit(
                project.id,
                project.primary_canvas_id,
                {"expected_row_version": saved["row_version"], "source_document": document},
                "live-seed-private-node",
            )
            content = (
                "创建一个文本节点和一个图片节点并连线：文本节点标题为林晚设定，正文为城市寻亲；"
                "图片节点标题为车站重逢，提示词为雨夜的车站。只搭建，不生成任何媒体。"
            )
        else:
            AssetService(session).create(
                {
                    "kind": "prop",
                    "name": "未选入旧素材",
                    "description": "PRIVATE_UNSELECTED_BODY",
                    "prompt": "PRIVATE_UNSELECTED_PROMPT",
                },
                project_id=project.id,
            )
            content = (
                "创建一个角色和一个分镜并关联：角色姓名为林晚，描述为寻找弟弟的姐姐；"
                "分镜标题为车站重逢，正文为林晚走进车站。只搭建，不生成任何媒体。"
            )
        conversation = service.create_global_conversation({}, "live-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {"content": content, "model_config_id": model, "execution_target": target},
            "live-structure-message",
        )
    gateway = _ObservedGateway(settings)
    runtime = AgentRuntime(factory, settings, gateway)
    _finish(runtime, accepted.run.id)
    with factory() as session:
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.task_id == accepted.task.id)
        ).all()
        writes = [
            row
            for row in operations
            if row.tool_name in {"apply_standard_patch", "apply_canvas_patch"}
        ]
        assert writes and all(row.status == "succeeded" for row in writes)
        assert not any(row.tool_name == "submit_generation_batch" for row in operations)
        if mode == "standard":
            assets = session.scalars(
                select(Asset).where(Asset.project_id == project.id, Asset.kind == "character")
            ).all()
            shots = session.scalars(
                select(ShotScript).where(ShotScript.project_id == project.id)
            ).all()
            assert len(assets) == len(shots) == 1
            assert assets[0].name == "林晚" and shots[0].title == "车站重逢"
        else:
            nodes = session.scalars(
                select(CanvasNode).where(CanvasNode.project_id == project.id)
            ).all()
            assert len(nodes) == 3 and {node.kind for node in nodes} == {"text", "image"}
        before = len(operations)
        version = session.get(AgentRun, accepted.run.id).message_version
    runtime.execute_one(accepted.run.id, version)
    assert "PRIVATE_UNSELECTED" not in json.dumps(gateway.inputs, ensure_ascii=False)
    with factory() as session:
        assert len(session.scalars(select(AgentOperation)).all()) == before
    _write_report(tmp_path, gateway, mode=mode, durable_operations=before)


def test_global_assistant_agnes_reads_real_memory_before_answering(global_live_workspace, tmp_path):
    factory, settings, model = global_live_workspace
    with factory() as session:
        service = task_tests.task_service(session, settings)
        memory = AgentMemoryService(session).create_item(
            {
                "scope_type": "user",
                "kind": "preference",
                "memory_key": "dialogue-rhythm",
                "title": "对白节奏",
                "aliases": ["说话风格"],
                "content": "每句对白尽量短，人物交锋要有停顿。",
            },
            "live-memory",
        )
        conversation = service.create_global_conversation({}, "live-memory-conversation")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "我之前要求的说话风格是什么？请核对记忆再简短回答。",
                "model_config_id": model,
            },
            "live-memory-question",
        )
    gateway = _ObservedGateway(settings)
    _finish(AgentRuntime(factory, settings, gateway), accepted.run.id)
    with factory() as session:
        reads = session.scalars(
            select(AgentOperation).where(AgentOperation.tool_name == "read_memory")
        ).all()
        assert reads and all(row.status == "succeeded" for row in reads)
        assert str(memory.id) in json.dumps([row.arguments for row in reads])
        answer = session.scalar(
            select(AgentMessage.content).where(AgentMessage.role == "assistant")
        )
        assert "短" in answer and "停顿" in answer
    _write_report(tmp_path, gateway, checked_memory_id=str(memory.id), answer=answer)


def test_global_assistant_agnes_task_compaction_keeps_early_constraints(
    global_live_workspace, tmp_path
):
    factory, settings, _model = global_live_workspace
    _conversation, accepted, _store, _claim, job = task_tests.task_constraint_job(
        global_live_workspace, repeat=80
    )
    gateway = _ObservedGateway(settings)
    AgentMemoryRuntime(factory, settings, gateway).execute_one(job.id, 1)
    with factory() as session:
        settled = session.get(AgentMemoryJob, job.id)
        assert settled.status == "succeeded", settled.error
        task = session.get(AgentTask, accepted.task.id)
        facts = task.effective_state["input_compaction"]["facts"]
        text = json.dumps(facts, ensure_ascii=False)
        assert "城市" in text and "杀" in text
        assert task.goal == "主角不能杀人。背景是城市。"
    _write_report(tmp_path, gateway, job_id=str(job.id), facts=facts)


def test_global_assistant_agnes_asks_about_incompatible_manual_methods(
    global_live_workspace, tmp_path
):
    factory, settings, model = global_live_workspace
    with factory() as session:
        service = task_tests.task_service(session, settings)
        skills = []
        for index, (title, ending) in enumerate((("团圆方法", "团圆"), ("永别方法", "永别"))):
            skills.append(
                AgentSkillService(session, settings).create(
                    {
                        "name": title,
                        "slug": f"ending-{index}",
                        "description": f"结尾必须{ending}",
                        "instructions": f"任何故事的结局必须是兄妹{ending}，不可更改。",
                    },
                    f"live-skill-{index}",
                )
            )
        conversation = service.create_global_conversation({}, "live-methods")
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "帮我构思林晚寻找弟弟的结尾。两份手动方法都已选入，暂时没有主要方法。",
                "model_config_id": model,
                "skills": [
                    {"id": skill.id, "content_version": str(skill.content_version)}
                    for skill in skills
                ],
            },
            "live-method-conflict",
        )
    gateway = _ObservedGateway(settings)
    _finish(AgentRuntime(factory, settings, gateway), accepted.run.id)
    with factory() as session:
        answer = session.scalar(
            select(AgentMessage.content).where(AgentMessage.role == "assistant")
        )
        assert "团圆" in answer and "永别" in answer
        assert any(word in answer for word in ("选择", "优先", "主要", "为准"))
        assert "？" in answer or "?" in answer
        task = session.get(AgentTask, accepted.task.id)
        assert (
            len([item for item in task.effective_state["skill_activations"] if item["active"]]) == 2
        )
    _write_report(tmp_path, gateway, answer=answer)
