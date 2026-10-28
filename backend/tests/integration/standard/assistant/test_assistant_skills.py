"""Real MySQL task methods; model responses and immutable storage are explicit substitutes."""

import io
import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

import short_drama.agent.skill.runtime as skill_runtime
import tests.integration.standard.assistant.test_global_assistant_tasks as task_tests
from short_drama.agent.coordinator.recovery import recover
from short_drama.agent.runtime.decision_input import prepare_decision
from short_drama.agent.runtime.decisions import execute_tools
from short_drama.agent.runtime.executor import AgentRuntimeStore
from short_drama.agent.runtime.operations import claim_operation, complete_operation
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.domain.skills.catalog.skill import AgentSkillVersion
from short_drama.service.base import utcnow
from short_drama.service.skills.catalog.skill import AgentSkillService
from short_drama.service.skills.catalog.versions import freeze_skills
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    task_actor,
    task_service,
)

pytestmark = pytest.mark.integration
task_workspace = task_tests.task_workspace


class _Storage:
    def __init__(self, factory):
        self.factory, self.objects, self.reads = factory, {}, 0
        self.fail = False

    def stat(self, bucket, key):
        from types import SimpleNamespace

        assert self.factory.kw["bind"].pool.checkedout() == 0
        if (bucket, key) not in self.objects:
            raise NotFound("object missing")
        return SimpleNamespace(bucket=bucket, object_name=key)

    def put(self, bucket, key, source, size, _mime):
        assert self.factory.kw["bind"].pool.checkedout() == 0
        self.objects[bucket, key] = source.read()
        assert len(self.objects[bucket, key]) == size
        return self.stat(bucket, key)

    @contextmanager
    def open(self, bucket, key):
        assert self.factory.kw["bind"].pool.checkedout() == 0
        self.reads += 1
        if self.fail:
            raise OSError("fixture storage offline")
        with io.BytesIO(self.objects[bucket, key]) as stream:
            yield stream


@pytest.fixture
def skill_workspace(task_workspace, monkeypatch):
    factory, settings, model = task_workspace
    settings.minio_image_bucket = "fixture-skill-text"
    storage = _Storage(factory)
    monkeypatch.setattr(
        skill_runtime,
        "read_resource_bytes",
        lambda configured, manifest: AgentSkillService(None, configured, storage)._resource_bytes(
            manifest
        ),
    )
    return factory, settings, model, storage


def create_skill(workspace, *, owner=1, **changes):
    factory, settings, _model, storage = workspace
    with factory() as session:
        session.info["actor"] = task_actor(owner)
        return AgentSkillService(session, settings, storage).create(
            {
                "name": "雨夜方法",
                "slug": "rain-method",
                "description": "依据主动选入内容核对人物动机。",
                "instructions": "先看用户提供的输入，再按[模板](assets/method.txt)组织对白。",
                "resources": [
                    {
                        "path": "assets/method.txt",
                        "kind": "template",
                        "content": "角色目标：旧版参考。",
                    }
                ],
                **changes,
            },
            uuid4().hex,
        )


def test_old_script_resource_is_rejected_by_catalog_and_resource_read(skill_workspace):
    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    with factory() as session:
        version = session.scalar(
            select(AgentSkillVersion).where(AgentSkillVersion.skill_key == skill.id)
        )
        resources = [dict(item, kind="script") for item in version.resources]
        version_id = version.id
    with factory.kw["bind"].begin() as connection:
        connection.execute(
            AgentSkillVersion.__table__.update()
            .where(AgentSkillVersion.id == version_id)
            .values(resources=resources)
        )
    for operation in (
        lambda service: service.detail(skill.id),
        lambda service: service.resource_content(
            skill.id, skill.content_version, "assets/method.txt"
        ),
    ):
        with factory() as session:
            session.info["actor"] = task_actor()
            with pytest.raises(WorkflowError) as error:
                operation(AgentSkillService(session, settings, storage))
            assert error.value.code == "invalid_agent_skill_resource"
            assert error.value.status_code == 409


def test_missing_skill_version_is_not_created_while_reading_or_selecting(skill_workspace):
    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    with factory.kw["bind"].begin() as connection:
        connection.execute(
            AgentSkillVersion.__table__.delete().where(AgentSkillVersion.skill_key == skill.id)
        )
    for operation in (
        lambda service: service.detail(skill.id),
        lambda service: service.patch(skill.id, {"row_version": skill.row_version, "name": "修改"}),
    ):
        with factory() as session:
            session.info["actor"] = task_actor()
            with pytest.raises(WorkflowError) as error:
                operation(AgentSkillService(session, settings, storage))
            assert error.value.code == "agent_skill_version_missing"
    with factory.begin() as session:
        session.info["actor"] = task_actor()
        with pytest.raises(WorkflowError) as error:
            freeze_skills(
                session, 1, [SimpleNamespace(id=skill.id, content_version=skill.content_version)]
            )
        assert error.value.code == "agent_skill_version_missing"
        assert (
            session.scalar(select(AgentSkillVersion).where(AgentSkillVersion.skill_key == skill.id))
            is None
        )


def new_skill_task(workspace, skill=None):
    factory, settings, model, _storage = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "讨论雨夜人物，不写入作品。",
                "model_config_id": model,
                **(
                    {"skills": [{"id": skill.id, "content_version": skill.content_version}]}
                    if skill
                    else {}
                ),
            },
            uuid4().hex,
        )
    return conversation, accepted


def queue_tools(workspace, accepted, calls):
    factory, settings, _model, _storage = workspace
    with factory() as session:
        version = session.get(AgentRun, accepted.run.id).message_version
    store = AgentRuntimeStore(factory, settings)
    claim = store.claim_execution(accepted.run.id, version, prepare_decision)
    assert claim is not None
    store.before_send(claim, {"fixture": True})
    store.save_raw(claim, {"fixture": True})
    assert store.save_result(
        claim,
        {
            "output_kind": "tool_requests",
            "output": {
                "calls": [
                    {"tool_call_id": uuid4().hex, "tool_name": name, "args": args}
                    for name, args in calls
                ]
            },
            "history": {"fixture_history": True},
            "usage": {"output_tokens": 1, "output_tokens_reported": True},
        },
    )
    return claim


def queue_tool(workspace, accepted, name, args):
    return queue_tools(workspace, accepted, [(name, args)])


def run_tool(workspace, accepted, name, args):
    queue_tool(workspace, accepted, name, args)
    factory, settings, _model, _storage = workspace
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        return session.scalar(
            select(AgentOperation)
            .where(
                AgentOperation.run_id == accepted.run.id,
            )
            .order_by(AgentOperation.id.desc())
            .limit(1)
        )


def test_manual_skill_and_resources_survive_update_disable_delete_and_explicit_resume(
    skill_workspace,
):
    factory, settings, _model, _storage = skill_workspace
    skill = create_skill(skill_workspace)
    conversation, accepted = new_skill_task(skill_workspace, skill)
    with factory() as session:
        session.info["actor"] = task_actor()
        library = AgentSkillService(session, settings, skill_workspace[3])
        updated = library.patch(
            skill.id, {"row_version": "1", "instructions": "全新的方法，不应回写原任务。"}
        )
        disabled = library.patch_settings(skill.id, {"row_version": "1", "enabled": False})
        assert disabled.content_version == 2
        library.delete(skill.id, {"row_version": str(updated.row_version)})
    result = run_tool(skill_workspace, accepted, "describe_creation_target", {})
    assert result.status == "succeeded"
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        active = skill_runtime.skill_context(task)
        assert active[0]["instructions"] == skill.instructions
        activation = active[0]["activation_id"]
    read = run_tool(
        skill_workspace,
        accepted,
        "read_skill_resource",
        {
            "activation_id": activation,
            "path": "assets/method.txt",
            "limit": 6,
        },
    )
    assert read.status == "succeeded" and read.result["content"] == "角色目标：旧"
    with factory() as session:
        service = task_service(session, settings)
        stopped = service.control_task(accepted.task.id, {"kind": "stop"}, "stop")
        resumed = service.control_task(
            accepted.task.id,
            {
                "kind": "resume",
                "expected_control_seq": str(stopped.task.control_seq),
            },
            "resume",
        )
    resumed_read = run_tool(
        skill_workspace,
        resumed,
        "read_skill_resource",
        {
            "activation_id": activation,
            "path": "assets/method.txt",
            "offset": 6,
        },
    )
    assert resumed_read.status == "succeeded" and resumed_read.result["content"] == "版参考。"
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert len(skill_runtime.activations(task)) == 1
        assert len(skill_runtime.skill_context(task)[0]["read_resources"]) == 2
        assert task.conversation_id == conversation.id


def test_auto_catalog_is_summary_only_and_activation_rechecks_current_settings(skill_workspace):
    factory, settings, _model, _storage = skill_workspace
    skill = create_skill(skill_workspace)
    manual = create_skill(skill_workspace, name="另一种方法")
    _conversation, accepted = new_skill_task(skill_workspace)
    catalog = run_tool(skill_workspace, accepted, "search_skills", {"query": "方法"})
    assert catalog.status == "succeeded"
    assert str(skill.id) in {item["id"] for item in catalog.result["items"]}
    assert str(manual.id) in {item["id"] for item in catalog.result["items"]}
    assert all(
        "instructions" not in item and "resources" not in item for item in catalog.result["items"]
    )
    with factory() as session:
        session.info["actor"] = task_actor()
        AgentSkillService(session, settings).patch_settings(
            skill.id, {"row_version": "1", "enabled": False}
        )
    rejected = run_tool(
        skill_workspace,
        accepted,
        "load_skill",
        {
            "skill_id": str(skill.id),
            "content_version": "1",
        },
    )
    assert rejected.status == "failed" and rejected.error["code"] == "agent_skill_disabled"
    with factory() as session:
        assert skill_runtime.activations(session.get(AgentTask, accepted.task.id)) == []


def test_manual_supplement_retains_own_frozen_version_and_can_add_another_method(skill_workspace):
    factory, settings, _model, storage = skill_workspace
    original = create_skill(skill_workspace)
    added = create_skill(skill_workspace, name="新增对白方法")
    conversation, accepted = new_skill_task(skill_workspace, original)
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        session.info["actor"] = task_actor()
        library = AgentSkillService(session, settings, storage)
        updated = library.patch(original.id, {"row_version": "1", "instructions": "新版正文"})
        library.delete(original.id, {"row_version": str(updated.row_version)})
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id,
            {
                "task_id": accepted.task.id,
                "content": "保留原方法，再增加对白方法。",
                "skills": [
                    {"id": str(item.id), "content_version": "1"} for item in (original, added)
                ],
            },
            uuid4().hex,
        )
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        current = skill_runtime.skill_context(session.get(AgentTask, accepted.task.id))
        assert {item["skill_id"] for item in current} == {str(original.id), str(added.id)}
        assert (
            next(item for item in current if item["skill_id"] == str(original.id))["instructions"]
            == original.instructions
        )


def test_task_frozen_method_cannot_bypass_new_activation_or_pending_removal(skill_workspace):
    from short_drama.core.exceptions import WorkflowError

    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    conversation, accepted = new_skill_task(skill_workspace, skill)
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        session.info["actor"] = task_actor()
        AgentSkillService(session, settings, storage).patch_settings(
            skill.id, {"row_version": "1", "enabled": False}
        )
    other_conversation, other = new_skill_task(skill_workspace)
    with factory() as session:
        service = task_service(session, settings)
        selection = {"skills": [{"id": str(skill.id), "content_version": "1"}]}
        with pytest.raises(WorkflowError, match="停用"):
            service.send_task_message(
                other_conversation.id,
                {"task_id": other.task.id, "content": "不能借用其他任务版本", **selection},
                uuid4().hex,
            )
        service.send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "移除所有手动方法", "skills": []},
            uuid4().hex,
        )
        with pytest.raises(WorkflowError, match="停用"):
            service.send_task_message(
                conversation.id,
                {"task_id": accepted.task.id, "content": "移除后不能绕过停用", **selection},
                uuid4().hex,
            )


def test_builtin_publication_uses_db_versions_and_keeps_existing_task_snapshot(skill_workspace):
    from scripts.agent_system_migration import backfill_skill_packages
    from short_drama.agent.skill.packages import builtin_skill_packages
    from short_drama.service.skills.catalog.builtin_skill import publish_builtin_skills

    factory, settings, _model, storage = skill_workspace
    with factory.kw["bind"].begin() as connection:
        backfill_skill_packages(connection)
    publish_builtin_skills(factory, settings, storage)
    with factory() as session:
        session.info["actor"] = task_actor()
        old_skill = AgentSkillService(session, settings, storage).detail("script.v1")
    _conversation, accepted = new_skill_task(skill_workspace, old_skill)
    package = next(item for item in builtin_skill_packages() if item.key == "script.v1")
    revised = package.model_copy(
        update={
            "content_version": 3,
            "content": package.content.model_copy(
                update={"instructions": package.content.instructions + "\n补充节奏要求。"}
            ),
        }
    )
    publish_builtin_skills(factory, settings, storage, [revised])
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        original = skill_runtime.skill_context(session.get(AgentTask, accepted.task.id))
        assert original[0]["instructions"] == old_skill.instructions
        assert original[0]["content_version"] == "2"
    with factory() as session:
        session.info["actor"] = task_actor()
        current = AgentSkillService(session, settings, storage).detail("script.v1")
        assert current.content_version == 3 and current.resource_count == 3
    _fresh_conversation, fresh = new_skill_task(skill_workspace, current)
    run_tool(skill_workspace, fresh, "describe_creation_target", {})
    with factory() as session:
        active = skill_runtime.skill_context(session.get(AgentTask, fresh.task.id))
        assert active[0]["instructions"] == current.instructions
        assert active[0]["content_version"] == "3"
    read = run_tool(
        skill_workspace,
        fresh,
        "read_skill_resource",
        {"activation_id": active[0]["activation_id"], "path": "assets/output-template.md"},
    )
    assert read.status == "succeeded" and read.result["content"].startswith("# 剧本标题")


def test_runtime_directory_and_guessed_loads_do_not_cross_accounts(skill_workspace):
    from short_drama.domain.identity.accounts.user import User

    factory, _settings, _model, _storage = skill_workspace
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="skill-second",
                display_name="Second",
                email="skill-second@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    foreign = create_skill(skill_workspace, owner=2, name="跨账号私有雨夜方法")
    own = create_skill(skill_workspace, name="本人雨夜方法")
    _conversation, accepted = new_skill_task(skill_workspace)
    catalog = run_tool(skill_workspace, accepted, "search_skills", {"query": "雨夜"})
    assert {item["id"] for item in catalog.result["items"]} == {str(own.id)}
    rejected = run_tool(
        skill_workspace,
        accepted,
        "load_skill",
        {"skill_id": str(foreign.id), "content_version": "1"},
    )
    assert rejected.error["code"] == "assistant_skill_not_discovered"


def test_manual_method_takes_budget_priority_without_discarding_its_input(skill_workspace):
    factory, settings, model, _storage = skill_workspace
    skills = [create_skill(skill_workspace, name=f"雨夜方法 {index}") for index in range(9)]
    conversation, accepted = new_skill_task(skill_workspace)
    run_tool(skill_workspace, accepted, "search_skills", {"query": "雨夜", "limit": 20})
    queue_tools(
        skill_workspace,
        accepted,
        [
            ("load_skill", {"skill_id": str(skill.id), "content_version": "1"})
            for skill in skills[:8]
        ],
    )
    execute_tools(factory, settings, accepted.run.id)
    with factory() as session:
        assert len(skill_runtime.skill_context(session.get(AgentTask, accepted.task.id))) == 8
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id,
            {
                "task_id": accepted.task.id,
                "content": "以后优先使用我指定的方法。",
                "model_config_id": model,
                "skills": [{"id": str(skills[8].id), "content_version": "1"}],
            },
            uuid4().hex,
        )
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        context = skill_runtime.skill_context(session.get(AgentTask, accepted.task.id))
        assert len(context) == 8
        assert context[0]["source"] == "manual" and context[0]["skill_id"] == str(skills[8].id)
    blocked = run_tool(
        skill_workspace,
        accepted,
        "load_skill",
        {"skill_id": str(skills[7].id), "content_version": "1"},
    )
    assert blocked.error["code"] == "assistant_skill_budget"
    with factory() as session:
        assert (
            skill_runtime.skill_context(session.get(AgentTask, accepted.task.id))[0]["source"]
            == "manual"
        )


def test_resource_context_budget_keeps_prior_fragments_and_reassembles_after_history_reset(
    skill_workspace,
):
    factory, settings, _model, _storage = skill_workspace
    skill = create_skill(
        skill_workspace,
        resources=[{"path": "assets/method.txt", "kind": "template", "content": "角" * 20000}],
    )
    _conversation, accepted = new_skill_task(skill_workspace, skill)
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        activation = skill_runtime.activations(session.get(AgentTask, accepted.task.id))[0]["id"]
    args = {"activation_id": activation, "path": "assets/method.txt", "limit": 4000}
    for offset in (0, 4000, 0):
        read = run_tool(
            skill_workspace, accepted, "read_skill_resource", {**args, "offset": offset}
        )
        assert read.status == "succeeded"
    exhausted = run_tool(skill_workspace, accepted, "read_skill_resource", {**args, "offset": 8000})
    assert exhausted.error["code"] == "assistant_skill_resource_budget"
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        run.checkpoint = {**run.checkpoint, "history": None, "pending_results": None}
        context = skill_runtime.skill_context(session.get(AgentTask, accepted.task.id))
        assert len(context[0]["read_resources"]) == 2
    claim = queue_tool(skill_workspace, accepted, "describe_creation_target", {})
    prepared = json.dumps(claim.inputs, ensure_ascii=False)
    assert skill.instructions in prepared and "角" * 4000 in prepared
    assert "storage_key" not in prepared and "bucket" not in prepared
    execute_tools(factory, settings, accepted.run.id)


def test_disable_race_is_ordered_by_the_durable_activation(skill_workspace):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    _conversation, accepted = new_skill_task(skill_workspace)
    run_tool(skill_workspace, accepted, "search_skills", {"query": "雨夜"})
    claim = queue_tool(
        skill_workspace, accepted, "load_skill", {"skill_id": str(skill.id), "content_version": "1"}
    )
    fence = claim.version, claim.token
    operation = claim_operation(factory, settings, accepted.run.id, fence=fence)
    barrier = Barrier(2)

    def load():
        barrier.wait(timeout=10)
        complete_operation(factory, settings, accepted.run.id, operation, fence=fence)

    def disable():
        barrier.wait(timeout=10)
        with factory() as session:
            session.info["actor"] = task_actor()
            AgentSkillService(session, settings, storage).patch_settings(
                skill.id, {"row_version": "1", "enabled": False}
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(load), pool.submit(disable)]
        for future in futures:
            future.result(timeout=20)
    with factory() as session:
        operation = session.get(AgentOperation, operation)
        active = skill_runtime.activations(session.get(AgentTask, accepted.task.id))
        if operation.status == "succeeded":
            assert len(active) == 1 and active[0]["skill"]["instructions"] == skill.instructions
        else:
            assert operation.error["code"] == "agent_skill_disabled" and active == []
        assert session.get(AgentRun, accepted.run.id).status == "running"


def test_auto_activation_deduplicates_and_keeps_original_version_after_directory_changes(
    skill_workspace,
):
    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    _conversation, accepted = new_skill_task(skill_workspace)
    run_tool(skill_workspace, accepted, "search_skills", {"query": "雨夜"})
    args = {"skill_id": str(skill.id), "content_version": "1", "purpose": "检查对白动机"}
    first = run_tool(skill_workspace, accepted, "load_skill", args)
    assert first.status == "succeeded" and first.result["source"] == "auto"
    with factory() as session:
        session.info["actor"] = task_actor()
        library = AgentSkillService(session, settings, storage)
        library.patch(skill.id, {"row_version": "1", "instructions": "新版本方法"})
        library.patch_settings(skill.id, {"row_version": "1", "enabled": False})
    repeated = run_tool(skill_workspace, accepted, "load_skill", args)
    assert repeated.result["already_loaded"] is True
    assert repeated.result["activation_id"] == first.result["activation_id"]
    assert repeated.result["instructions"] == skill.instructions
    _other, fresh = new_skill_task(skill_workspace)
    guessed = run_tool(skill_workspace, fresh, "load_skill", args)
    assert guessed.error["code"] == "assistant_skill_not_discovered"


def test_legacy_task_bootstrap_uses_frozen_body_and_the_original_manual_source(skill_workspace):
    factory, settings, model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    conversation, accepted = new_skill_task(skill_workspace, skill)
    initial = run_tool(skill_workspace, accepted, "describe_creation_target", {})
    assert initial.result["skill_activations"][0]["content_version"] == "1"
    with factory() as session:
        session.info["actor"] = task_actor()
        AgentSkillService(session, settings, storage).patch(
            skill.id, {"row_version": "1", "instructions": "部署后发布的新方法"}
        )
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        task.effective_state = {
            key: value for key, value in task.effective_state.items() if key != "skill_activations"
        }
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id,
            {"task_id": accepted.task.id, "content": "继续核对动作。", "model_config_id": model},
            uuid4().hex,
        )
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        active = skill_runtime.activations(session.get(AgentTask, accepted.task.id))
        assert len(active) == 1 and active[0]["skill"]["instructions"] == skill.instructions
        assert active[0]["source_message_id"] == str(accepted.message.id)


def test_resource_read_is_task_scoped_and_storage_failure_preserves_activation(skill_workspace):
    factory, _settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    _conversation, accepted = new_skill_task(skill_workspace, skill)
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        activation = skill_runtime.activations(session.get(AgentTask, accepted.task.id))[0]["id"]
    _other, fresh = new_skill_task(skill_workspace)
    denied = run_tool(
        skill_workspace,
        fresh,
        "read_skill_resource",
        {
            "activation_id": activation,
            "path": "assets/method.txt",
        },
    )
    assert denied.status == "failed"
    storage.fail = True
    failed = run_tool(
        skill_workspace,
        accepted,
        "read_skill_resource",
        {
            "activation_id": activation,
            "path": "assets/method.txt",
        },
    )
    assert failed.error["code"] == "agent_skill_resource_unavailable"
    storage.fail = False
    restored = run_tool(
        skill_workspace,
        accepted,
        "read_skill_resource",
        {
            "activation_id": activation,
            "path": "assets/method.txt",
        },
    )
    assert restored.result["content"] == "角色目标：旧版参考。"


def test_stop_after_resource_claim_waits_for_read_and_blocks_next_tool(skill_workspace):
    factory, settings, _model, _storage = skill_workspace
    skill = create_skill(skill_workspace)
    _conversation, accepted = new_skill_task(skill_workspace, skill)
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        activation = skill_runtime.activations(session.get(AgentTask, accepted.task.id))[0]["id"]
    queue_tool(
        skill_workspace,
        accepted,
        "read_skill_resource",
        {
            "activation_id": activation,
            "path": "assets/method.txt",
        },
    )
    operation = claim_operation(factory, settings, accepted.run.id)
    with factory() as session:
        stopped = task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, "stop"
        )
        assert stopped.task.control_state == "stop_pending"
    complete_operation(factory, settings, accepted.run.id, operation)
    with factory() as session:
        saved = session.get(AgentOperation, operation)
        assert saved.status == "succeeded" and saved.result["content"] == "角色目标：旧版参考。"
        assert session.get(AgentTask, accepted.task.id).status == "stopped"
    assert claim_operation(factory, settings, accepted.run.id) is None


def test_auto_load_does_not_replace_an_active_version_after_a_new_search(skill_workspace):
    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    _conversation, accepted = new_skill_task(skill_workspace)
    run_tool(skill_workspace, accepted, "search_skills", {"query": "雨夜"})
    original = run_tool(
        skill_workspace, accepted, "load_skill", {"skill_id": str(skill.id), "content_version": "1"}
    )
    assert original.status == "succeeded"
    with factory() as session:
        session.info["actor"] = task_actor()
        AgentSkillService(session, settings, storage).patch(
            skill.id, {"row_version": "1", "instructions": "新发布内容不能自动改变任务方法"}
        )
    run_tool(skill_workspace, accepted, "search_skills", {"query": "雨夜"})
    changed = run_tool(
        skill_workspace, accepted, "load_skill", {"skill_id": str(skill.id), "content_version": "2"}
    )
    assert changed.status == "failed"
    assert changed.error["code"] == "assistant_skill_version_frozen"
    with factory() as session:
        active = skill_runtime.skill_context(session.get(AgentTask, accepted.task.id))
        assert len(active) == 1 and active[0]["instructions"] == skill.instructions


def test_removed_manual_method_stays_excluded_and_plain_supplements_keep_it(skill_workspace):
    factory, settings, model, _storage = skill_workspace
    skill = create_skill(skill_workspace)
    conversation, accepted = new_skill_task(skill_workspace, skill)
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id,
            {
                "task_id": accepted.task.id,
                "content": "还要保留克制的对白。",
                "model_config_id": model,
            },
            uuid4().hex,
        )
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        active = skill_runtime.skill_context(session.get(AgentTask, accepted.task.id))
        assert len(active) == 1 and active[0]["source"] == "manual"
    with factory() as session:
        task_service(session, settings).send_task_message(
            conversation.id,
            {
                "task_id": accepted.task.id,
                "content": "移除这项方法，改用直接讨论。",
                "model_config_id": model,
                "skills": [],
            },
            uuid4().hex,
        )
    rejected = run_tool(
        skill_workspace, accepted, "load_skill", {"skill_id": str(skill.id), "content_version": "1"}
    )
    assert rejected.error["code"] == "assistant_skill_excluded"
    with factory() as session:
        task = session.get(AgentTask, accepted.task.id)
        assert skill_runtime.skill_context(task) == []
        assert not skill_runtime.activations(task)[0]["active"]
    catalog = run_tool(skill_workspace, accepted, "search_skills", {"query": "雨夜", "limit": 1})
    assert catalog.result["items"] == [] and catalog.result["total"] == 0


def test_resource_result_from_replaced_worker_is_discarded_even_without_explicit_fence(
    skill_workspace, monkeypatch
):
    factory, settings, _model, _storage = skill_workspace
    skill = create_skill(skill_workspace)
    _conversation, accepted = new_skill_task(skill_workspace, skill)
    run_tool(skill_workspace, accepted, "describe_creation_target", {})
    with factory() as session:
        activation = skill_runtime.activations(session.get(AgentTask, accepted.task.id))[0]["id"]
    queue_tool(
        skill_workspace,
        accepted,
        "read_skill_resource",
        {"activation_id": activation, "path": "assets/method.txt"},
    )
    operation = claim_operation(factory, settings, accepted.run.id)
    original_reader = skill_runtime.read_resource_bytes
    replacement = []

    def read_and_replace(configured, manifest):
        content = original_reader(configured, manifest)
        with factory.begin() as session:
            session.get(AgentRun, accepted.run.id).lease_until = utcnow() - timedelta(seconds=1)
        assert recover(factory, settings) == 1
        with factory() as session:
            version = session.get(AgentRun, accepted.run.id).message_version
        claim = AgentRuntimeStore(factory, settings).claim_execution(
            accepted.run.id, version, prepare_decision
        )
        assert claim is not None and claim.phase == "tools"
        replacement.append((claim.version, claim.token))
        assert (
            claim_operation(factory, settings, accepted.run.id, fence=replacement[0]) == operation
        )
        return content

    with monkeypatch.context() as patch:
        patch.setattr(skill_runtime, "read_resource_bytes", read_and_replace)
        complete_operation(factory, settings, accepted.run.id, operation)
    with factory() as session:
        assert session.get(AgentOperation, operation).status == "running"
        task = session.get(AgentTask, accepted.task.id)
        assert skill_runtime.skill_context(task)[0]["read_resources"] == []
    complete_operation(factory, settings, accepted.run.id, operation, fence=replacement[0])
    with factory() as session:
        assert session.get(AgentOperation, operation).status == "succeeded"


@pytest.mark.parametrize("change", ["version", "disabled"])
def test_load_rechecks_a_stale_search_result(skill_workspace, change):
    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    _conversation, accepted = new_skill_task(skill_workspace)
    run_tool(skill_workspace, accepted, "search_skills", {"query": "雨夜"})
    with factory() as session:
        session.info["actor"] = task_actor()
        library = AgentSkillService(session, settings, storage)
        if change == "version":
            library.patch(skill.id, {"row_version": "1", "instructions": "已更新的正文"})
        else:
            library.patch_settings(skill.id, {"row_version": "1", "enabled": False})
    rejected = run_tool(
        skill_workspace, accepted, "load_skill", {"skill_id": str(skill.id), "content_version": "1"}
    )
    assert rejected.status == "failed"
    assert rejected.error["code"] == ("conflict" if change == "version" else "agent_skill_disabled")
    with factory() as session:
        assert skill_runtime.activations(session.get(AgentTask, accepted.task.id)) == []
