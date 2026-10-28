"""Opt-in paid long task. Credentials stay in process memory; media generation is disabled."""

import base64
import hashlib
import io
import json
import os
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from short_drama.agent.coordinator.recovery import recover
from short_drama.agent.gateway.langchain import LangChainGateway
from short_drama.agent.gateway.protocol import AgentGatewayError
from short_drama.agent.harness.runtime import HarnessRuntime
from short_drama.agent.memory.runtime import AgentMemoryRuntime
from short_drama.agent.resources.contracts import assert_reference_payload
from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.core.time import utcnow
from short_drama.domain import Asset, MediaFile, ShotScript
from short_drama.domain.agent import AgentMessage, AgentRun, AgentToolCall, AgentTurn
from short_drama.domain.agent_graph import AgentGraphCheckpoint, AgentGraphWrite
from short_drama.domain.agent_memory import AgentMemoryJob
from short_drama.domain.agent_task import AgentOperation, AgentTask, AgentTaskInput
from short_drama.service.agent_skill_service import AgentSkillService
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.storage_service import StorageService
from short_drama.storage.minio import MinioStorage
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace,
    image_group_workspace,
    task_actor,
    task_service,
)
from tests.support.integration.harness_skill import seed_legacy_script

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_HARNESS_LIVE") != "1",
        reason="Set RUN_HARNESS_LIVE=1 with a process-only API key",
    ),
]
task_workspace = harness_workspace


class NamespaceStorage:
    def __init__(self, storage, prefix):
        self.storage, self.prefix = storage, prefix

    def key(self, key):
        return key if key.startswith(self.prefix) else self.prefix + key

    def put(self, bucket, key, *args):
        return self.storage.put(bucket, self.key(key), *args)

    def stat(self, bucket, key):
        return self.storage.stat(bucket, self.key(key))

    def open(self, bucket, key):
        return self.storage.open(bucket, self.key(key))


class MeteredGateway(LangChainGateway):
    def __init__(self, settings):
        super().__init__(settings)
        self.usage = []
        self.failures = []

    async def decide(self, *args, **kwargs):
        try:
            result = await super().decide(*args, **kwargs)
        except AgentGatewayError as error:
            self.failures.append({"code": error.code, "external_requests": error.requests})
            raise
        self.usage.append(result["usage"])
        return result


def test_real_model_long_creation_with_compaction_recovery_and_saved_results(
    task_workspace,
    monkeypatch,
):
    import short_drama.agent.context.transcript as transcript
    import short_drama.agent.skill.runtime as skill_runtime
    import short_drama.agent.task.compaction as compaction
    from short_drama.agent.resources.resolver import ResourceResolver

    factory, _fixture_settings, model = task_workspace
    secret = os.environ["HARNESS_LIVE_API_KEY"]
    root = os.environ["HARNESS_LIVE_URL"].rstrip("/")
    settings = Settings()
    settings.agent_enabled = True
    settings.encryption_key = base64.b64encode(os.urandom(32)).decode()
    settings.model_discovery_allowed_hosts = [urlsplit(root).hostname]
    cipher = KeyCipher(settings.encryption_key)
    with factory() as session:
        session.info["actor"] = task_actor()
        AIModelConfigService(session, cipher=cipher).update(
            model,
            {
                "row_version": "1",
                "model_key": os.environ["HARNESS_LIVE_MODEL"],
                "base_url": root + "/v1/chat/completions",
                "apikey": secret,
            },
        )
    storage = MinioStorage(settings)
    prefix = f"acceptance/harness/{uuid4().hex}/"
    objects = NamespaceStorage(storage, prefix)
    monkeypatch.setattr(transcript, "RECENT_TOOL_TOKENS", 2600)
    # Exercise real L4 compaction with a shorter fixture, rather than add megabytes
    # of meaningless prose. CAS, source coverage and settlement stay unchanged.
    monkeypatch.setattr(compaction, "SOFT_HISTORY_BYTES", 1000)
    monkeypatch.setattr(compaction, "RECENT_INPUT_BYTES", 600)
    monkeypatch.setattr(
        skill_runtime,
        "read_resource_bytes",
        lambda configured, manifest: AgentSkillService(None, configured, objects)._resource_bytes(
            manifest
        ),
    )
    gateway = MeteredGateway(settings)
    runtime = HarnessRuntime(
        factory,
        settings,
        gateway,
        resolver_factory=lambda: ResourceResolver(StorageService(objects, settings), settings),
    )
    memory = AgentMemoryRuntime(factory, settings, gateway)
    started = time.monotonic()
    try:
        resources = []
        clues = [
            "主人公叫林晚",
            "失物是铜钥匙",
            "地点是雨夜旧车站",
            "离站时间是零点",
            "误会来自同名包裹",
            "结尾应揭示钥匙属于失联的父亲",
        ]
        for index, clue in enumerate(clues):
            following = (
                f"下一步先确认本事实再读取 step-{index + 2}.md。"
                if index + 1 < len(clues)
                else "六段线索已齐备，下一步只读 scripts/check.py，再查看 assets/layout.png。"
            )
            resources.append(
                {
                    "path": f"references/step-{index + 1}.md",
                    "content": f"本步事实：{clue}。{following}\n"
                    + "节奏说明：以角色行为呈现线索，不通过长篇解释交代。\n" * 65,
                }
            )
        picture = Image.new("RGB", (160, 100), "white")
        ImageDraw.Draw(picture).rectangle((0, 0, 45, 99), fill="blue")
        pixels = io.BytesIO()
        picture.save(pixels, "PNG")
        resources.append(
            {
                "path": "assets/layout.png",
                "kind": "image",
                "encoding": "base64",
                "content": base64.b64encode(pixels.getvalue()).decode(),
            }
        )
        skills = []
        for index in range(2):
            with factory() as session:
                session.info["actor"] = task_actor()
                skills.append(
                    AgentSkillService(session, settings, objects).create(
                        {
                            "name": f"雨夜长任务方法{index + 1}",
                            "slug": f"harness-live-{index + 1}",
                            "description": "按阶段核对雨夜短剧资料，保存角色和分镜并用回执核验。",
                            "instructions": "逐段读取资料；用户明确约束优先。"
                            "完成后用实际操作回执核验写入。"
                            "脚本只读，图片需要看像素时才读取。",
                            "resources": resources if index == 0 else [],
                        },
                        uuid4().hex,
                    )
                )
        skills[0] = seed_legacy_script((factory, settings, model, objects), skills[0], task_actor())
        reference, image_groups = image_group_workspace((factory, settings, model))
        for group in image_groups:
            for image_item in group["items"]:
                object_key = prefix + f"selected/{image_item['media_id']}.png"
                stored = storage.put(
                    settings.minio_image_bucket,
                    object_key,
                    io.BytesIO(pixels.getvalue()),
                    len(pixels.getvalue()),
                    "image/png",
                )
                with factory.begin() as session:
                    media = session.get(MediaFile, int(image_item["media_id"]))
                    media.storage_locator = stored.storage_locator
                    media.byte_size = len(pixels.getvalue())
                    media.checksum_sha256 = hashlib.sha256(pixels.getvalue()).hexdigest()
                    media.width, media.height = picture.size
        goal = (
            "完成一项长任务：把雨夜旧车站的悬疑开场补齐并实际保存。为了逐步核验依赖，"
            "每个决策段只调用一个工具，拿到上一工具结果再继续下一项。\n"
            "先登记用户来源的交付要求和数量，随后启用需要的写入能力组。"
            "1. 分别加载手动选入的两个方法，再重复加载第一个方法，核对去重回执。\n"
            "2. 按次序分别读取第一个方法的 references/step-1.md 至 step-6.md，"
            "每段核对线索后，用 record_task_observation 将该段事实及读取原文引用保存为工作笔记，"
            "然后再读下一段；不要一轮批量读取。\n"
            "3. 只读 scripts/check.py 的规则，再查看 assets/layout.png 的实际像素，"
            "然后读取已选素材的当前图片组目录，通过 read_image_content 查看其中第一个图位，"
            "两种图片分别辨认，在最终回答说明蓝色条带位于哪一侧。\n"
            "4. 读取真实创作目标目录，再保存两个角色（林晚和乘客）及关联的三个连续分镜，"
            "每个分镜有标题和剧本文字。使用资料中的铜钥匙、零点、同名包裹和父亲线索。\n"
            "5. 对每个成功保存批次分别调用 read_task_results 读取成果，"
            "核对数量与正文，再给最终总结。"
            "需要旧细节时重读对应资料，不能把摘要或口头计划当作实际保存。"
            "不调用任何生图、生视频或音频生成工具。"
        )
        with factory() as session:
            service = task_service(session, settings)
            conversation = service.create_global_conversation({}, uuid4().hex)
            accepted = service.send_task_message(
                conversation.id,
                {
                    "content": goal,
                    "model_config_id": model,
                    "skills": [{"id": s.id, "content_version": s.content_version} for s in skills],
                    "execution_target": {"mode": "standard", "project_id": reference["project_id"]},
                    "subject_selection": {"expected_input_revision": "0", "subjects": [reference]},
                },
                uuid4().hex,
            )
            service.send_task_message(
                conversation.id,
                {
                    "task_id": accepted.task.id,
                    "content": "补充约束：悬疑来自人物误会，禁止超自然解释。全程不调用媒体生成。",
                },
                uuid4().hex,
            )
        settled = runtime.store.save_result
        crash = {"done": False}

        def crash_once(claim, *args, **kwargs):
            if not crash["done"] and len(gateway.usage) >= 7:
                crash["done"] = True
                raise RuntimeError("acceptance simulated worker loss after raw receipt")
            return settled(claim, *args, **kwargs)

        monkeypatch.setattr(runtime.store, "save_result", crash_once)
        max_dropped, max_retained = 0, 0
        continuations = []
        for _ in range(80):
            with factory() as session:
                jobs = [
                    (j.id, j.message_version)
                    for j in session.scalars(
                        select(AgentMemoryJob).where(
                            AgentMemoryJob.conversation_id == conversation.id,
                            AgentMemoryJob.status == "pending",
                            AgentMemoryJob.kind != "purge",
                        )
                    )
                ]
            for identifier, version in jobs:
                memory.execute_one(identifier, version)
            resume_error = None
            with factory.begin() as session:
                run = session.get(AgentRun, accepted.run.id)
                if run.status in {"succeeded", "failed", "cancelled"}:
                    code = (run.error or {}).get("code")
                    if (
                        run.status == "failed"
                        and not continuations
                        and code
                        in {
                            "agent_provider_error",
                            "agent_provider_unknown",
                            "agent_provider_rate_limited",
                            "timeout",
                            "connection_error",
                            "transport_error",
                            "incomplete_agent_response",
                        }
                    ):
                        resume_error = code
                    else:
                        assert run.status == "succeeded", {
                            "error": run.error,
                            "usage": run.usage,
                            "budget": run.budget,
                            "operations": [
                                (item.tool_name, item.status, item.error)
                                for item in session.scalars(
                                    select(AgentOperation)
                                    .where(AgentOperation.task_id == accepted.task.id)
                                    .order_by(AgentOperation.id)
                                )
                            ],
                        }
                        break
                # The test drives broker boundaries directly, without waiting
                # for scheduling backoff or running a production worker queue.
                if not resume_error:
                    run.next_run_at = utcnow()
                    version = run.message_version
            if resume_error:
                # Simulate one ordinary user continuation after a real transient
                # pause. This creates a new Run; it never replays the unknown POST.
                original_task, original_run = accepted.task.id, accepted.run.id
                with factory() as session:
                    accepted = task_service(session, settings).send_task_message(
                        conversation.id,
                        {
                            "task_id": accepted.task.id,
                            "content": "继续完成原任务，保留已有成果与已结算步骤。",
                        },
                        uuid4().hex,
                    )
                assert accepted.task.id == original_task and accepted.run.id != original_run
                continuations.append(resume_error)
                continue
            try:
                runtime.execute_one(accepted.run.id, version)
            except RuntimeError as error:
                if not crash["done"] or "database recovery required" not in str(error):
                    raise
                with factory.begin() as session:
                    session.get(AgentRun, accepted.run.id).lease_until = utcnow() - timedelta(
                        seconds=1
                    )
                assert recover(factory, settings) >= 1
            with factory() as session:
                metrics = session.get(AgentRun, accepted.run.id).checkpoint.get(
                    "context_metrics", {}
                )
                max_dropped = max(max_dropped, metrics.get("dropped_groups", 0))
                max_retained = max(max_retained, metrics.get("retained_skills", 0))
                progress = [
                    {
                        "tool": item.tool_name,
                        "status": item.status,
                        "error": item.error,
                        "path": item.arguments.get("path"),
                        "offset": item.arguments.get("offset"),
                        "next_offset": (item.result or {}).get("next_offset"),
                        "changes": len(item.changes),
                    }
                    for item in session.scalars(
                        select(AgentOperation)
                        .where(AgentOperation.task_id == accepted.task.id)
                        .order_by(AgentOperation.id)
                    )
                ]
                Path("../.runtime/harness-live-progress.json").write_text(
                    json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                decisions = [
                    {
                        "id": str(turn.id),
                        "status": turn.status,
                        "error": turn.error,
                        "has_raw_receipt": bool(turn.response.get("raw")),
                        "normalized": turn.response.get("normalized"),
                        "context_frame": turn.request_messages[0]
                        .get("kwargs", {})
                        .get("context_frame"),
                    }
                    for turn in session.scalars(
                        select(AgentTurn).where(
                            AgentTurn.run_id.in_(
                                select(AgentRun.id).where(AgentRun.task_id == accepted.task.id)
                            )
                        )
                    )
                ]
                assert_reference_payload(decisions)
                Path("../.runtime/harness-live-decisions.json").write_text(
                    json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        else:
            pytest.fail("Long task did not settle within its 80 test-driver boundaries")
        with factory() as session:
            operations = session.scalars(
                select(AgentOperation).where(AgentOperation.task_id == accepted.task.id)
            ).all()
            task = session.get(AgentTask, accepted.task.id)
            turns = session.scalars(
                select(AgentTurn).where(
                    AgentTurn.run_id.in_(
                        select(AgentRun.id).where(AgentRun.task_id == accepted.task.id)
                    )
                )
            ).all()
            successful = [item for item in operations if item.status == "succeeded"]
            assert len(turns) >= 8
            first_frame = min(turns, key=lambda item: item.id).request_messages[0]["kwargs"][
                "context_frame"
            ]
            assert any(
                item["content"] == goal and not item["truncated"]
                for item in first_frame["requirement_sources"]["items"]
            ), "The reviewer must be able to cite original requirements after real L4 compaction"
            assert all(
                len(turn.response["normalized"]["output"]["calls"]) == 1
                for turn in turns
                if (turn.response.get("normalized") or {}).get("output_kind") == "tool_requests"
            )
            assert crash["done"] and any(u["external_requests"] == 0 for u in gateway.usage)
            assert max_dropped > 0 and max_retained == 2
            assert task.effective_state.get("input_compaction"), "Real L4 compaction must settle"
            assert any(
                item.tool_name == "load_skill" and item.result.get("already_loaded")
                for item in successful
            )
            read_paths = [
                item.arguments["path"]
                for item in sorted(successful, key=lambda item: item.id)
                if item.tool_name == "read_skill_resource"
            ]
            stages = [f"references/step-{index}.md" for index in range(1, 7)]
            assert {*stages, "scripts/check.py", "assets/layout.png"} <= set(read_paths)
            first_reads = [read_paths.index(path) for path in stages]
            assert first_reads == sorted(first_reads)
            assert any(item.tool_name == "read_task_results" for item in successful), {
                "final_output": max(turns, key=lambda item: item.id).response["normalized"][
                    "output"
                ],
                "operations": [(item.tool_name, item.status, item.error) for item in operations],
            }
            assert any(item.tool_name == "read_image_content" for item in successful)
            writes = [item for item in successful if item.tool_name == "apply_standard_patch"]
            assert sum(len(item.changes) for item in writes) >= 5
            read_batches = {
                item.result["operation_id"]
                for item in successful
                if item.tool_name == "read_task_results"
            }
            assert {str(item.id) for item in writes} <= read_batches
            created = [
                change for item in writes for change in item.changes if change["before"] is None
            ]
            assets = [
                session.get(Asset, int(change["id"]))
                for change in created
                if change["kind"] == "asset"
            ]
            shots = [
                session.get(ShotScript, int(change["id"]))
                for change in created
                if change["kind"] == "shot"
            ]
            assert len(assets) == 2 and {item.kind for item in assets} == {"character"}
            assert any(item.name == "林晚" for item in assets)
            assert len(shots) == 3 and all(item.title and item.script for item in shots)
            story = "\n".join(item.script for item in shots)
            assert all(clue in story for clue in ("铜钥匙", "零点", "父亲")), story
            # The title can name the misunderstanding while dialogue shows the
            # matching name. Inspect both saved fields; the package must be in prose.
            assert any(
                "包裹" in item.script and "同名" in f"{item.title}\n{item.script}" for item in shots
            ), story
            assert all(
                change["after"]["asset_ids"] for change in created if change["kind"] == "shot"
            )
            assert not any(item.tool_name == "submit_generation_batch" for item in operations)
            run = session.get(AgentRun, accepted.run.id)
            runs = session.scalars(
                select(AgentRun).where(AgentRun.task_id == accepted.task.id)
            ).all()
            run_ids = [item.id for item in runs]
            for item in runs:
                assert_reference_payload(item.checkpoint)
            assert_reference_payload(task.effective_state)
            assert_reference_payload(task.context_snapshot)
            assert_reference_payload(task.selected_skills)
            for operation in operations:
                assert_reference_payload(operation.arguments)
                assert_reference_payload(operation.result)
                assert_reference_payload(operation.changes)
            for model, condition, fields in (
                (AgentToolCall, AgentToolCall.run_id.in_(run_ids), ("arguments", "result")),
                (AgentTaskInput, AgentTaskInput.task_id == task.id, ("payload",)),
                (AgentMessage, AgentMessage.task_id == task.id, ("content", "references")),
                (
                    AgentGraphCheckpoint,
                    AgentGraphCheckpoint.run_id.in_(run_ids),
                    ("checkpoint", "checkpoint_metadata"),
                ),
                (AgentGraphWrite, AgentGraphWrite.run_id.in_(run_ids), ("payload",)),
            ):
                for row in session.scalars(select(model).where(condition)):
                    for field in fields:
                        assert_reference_payload(getattr(row, field))
            for turn in turns:
                assert_reference_payload(turn.request_messages)
                assert_reference_payload(turn.response)
            final = session.scalar(
                select(AgentMessage.content)
                .where(
                    AgentMessage.task_id == task.id,
                    AgentMessage.kind == "reply",
                    AgentMessage.role == "assistant",
                )
                .order_by(AgentMessage.id.desc())
            )
            assert "蓝" in final and "左" in final, "Final answer must identify the observed stripe"
            assert task.effective_state["task_contract"]["verified"]
            assert len(task.effective_state["working_set"]["items"]) >= 6
            assert "standard_write" in task.effective_state["enabled_tool_groups"]
            reviews = [
                turn
                for turn in turns
                if turn.request_messages[0].get("kwargs", {}).get("decision_purpose")
                == "completion_review"
            ]
            assert reviews and all(turn.response["internal"] for turn in reviews)
            external_usage = [usage for usage in gateway.usage if usage["external_requests"] > 0]
            report = {
                "status": "passed",
                "model": os.environ["HARNESS_LIVE_MODEL"],
                "duration_seconds": round(time.monotonic() - started, 2),
                "turns": len(turns),
                "tool_calls": len(operations),
                "writes": len(writes),
                "max_dropped_groups": max_dropped,
                "retained_skills": max_retained,
                "raw_replay_requests": 0,
                "media_entity_leaks": 0,
                "saved_characters": len(assets),
                "saved_shots": len(shots),
                "visual_stripe_side": "left",
                "selected_image_read": True,
                "completion_reviews": len(reviews),
                "working_observations": len(task.effective_state["working_set"]["items"]),
                "verified_requirements": len(task.effective_state["task_contract"]["requirements"]),
                "task_runs": len(runs),
                "user_continuations": len(continuations),
                "continuation_reasons": continuations,
                "gateway_failures": gateway.failures,
                "cached_tokens": sum(item.usage.get("cached_tokens", 0) for item in runs),
                "run_input_tokens": sum(item.usage.get("input_tokens", 0) for item in runs),
                "external_requests": sum(u["external_requests"] for u in external_usage)
                + sum(u["external_requests"] for u in gateway.failures),
                "input_tokens": sum(u.get("input_tokens", 0) for u in external_usage),
                "output_tokens": sum(u.get("output_tokens", 0) for u in external_usage),
            }
            Path("../.runtime/harness-live-result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        for bucket in {settings.minio_image_bucket}:
            for item in storage.iter_objects(bucket, prefix, include_versions=True):
                storage.remove(bucket, item.object_name, version_id=item.version_id)
        storage.close()
