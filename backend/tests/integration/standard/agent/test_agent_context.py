"""Disposable MySQL context ownership; real MinIO is opt-in, models are never invoked."""

import hashlib
import io
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

import tests.support.assistant_workspace as conversation_tests
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.db.readiness import assert_agent_ready, assert_identity_ready, inspect_agent_schema
from short_drama.domain.assistant.context.context import AgentAttachment, AgentSkill
from short_drama.schemas.assistant.context.context import SkillSelection
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.assistant.context.attachment import AgentAttachmentService
from short_drama.service.skills.catalog.skill import AgentSkillService
from short_drama.service.skills.catalog.versions import freeze_skills
from short_drama.storage.minio import MinioStorage
from tests.support.assistant_workspace import actor, settings

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


def skills(session, owner=1):
    session.info["actor"] = actor(owner)
    return AgentSkillService(session, settings())


def attachments(session, owner=1):
    session.info["actor"] = actor(owner)
    storage = SimpleNamespace(
        presigned_get=lambda bucket, key, _: f"https://local.invalid/{bucket}/{key}"
    )
    return AgentAttachmentService(session, settings(), storage)


class TrackedAttachmentStorage(MinioStorage):
    def __init__(self, configured):
        super().__init__(configured)
        self.prefix = "agent-context-integration/" + uuid4().hex + "/"
        self.written = {}

    def stat(self, bucket, key):
        return super().stat(bucket, key if key.startswith(self.prefix) else self.prefix + key)

    def put(self, bucket, key, *args):
        key = self.prefix + key
        self.written[bucket, key] = None
        stored = super().put(bucket, key, *args)
        self.written[bucket, key] = stored.version_id
        return stored


@pytest.mark.skipif(
    os.environ.get("RUN_AGENT_INFRA_INTEGRATION") != "1",
    reason="Set RUN_AGENT_INFRA_INTEGRATION=1 for configured local MinIO",
)
def test_real_minio_skill_package_versions_keep_exact_resources(workspace):
    """Exercise immutable resources through the explicitly frozen legacy V2 engine."""
    from sqlalchemy import select

    from short_drama.agent.runtime.decision_input import prepare_decision
    from short_drama.agent.runtime.executor import AgentRuntimeStore
    from short_drama.agent.runtime.operations import claim_operation, complete_operation
    from short_drama.agent.skill.contracts import activations
    from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
    from short_drama.service.assistant.tasks.task import AssistantTaskService
    from tests.integration.standard.assistant.test_global_assistant_tasks import legacy_task_engine

    factory, project_id, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        model_id = (
            AIModelConfigService(session)
            .create(
                {
                    "service_type": "text",
                    "name": "Resource fixture",
                    "model_key": "fixture",
                    "provider": "compatible",
                    "base_url": "https://model.invalid/v1",
                }
            )
            .id
        )
    configured = Settings(auth_enabled=True, agent_enabled=True)
    storage = TrackedAttachmentStorage(configured)
    try:
        with legacy_task_engine(factory), factory() as session:
            session.info["actor"] = actor(1)
            library = AgentSkillService(session, configured, storage)
            original = library.create(
                {
                    "name": "真实资料测试",
                    "slug": "minio-resource-test",
                    "description": "只验证资料版本。",
                    "instructions": "按[资料](references/notes.md)检查输入。",
                    "resources": [
                        {"path": "references/notes.md", "content": "# 原资料\n仅供本次测试。"}
                    ],
                },
                "minio-package",
            )
            task_service = AssistantTaskService(session, configured)
            conversation = task_service.create_global_conversation({}, "minio-task-conversation")
            accepted = task_service.send_task_message(
                conversation.id,
                {
                    "content": "用指定方法核对资料，不写入作品。",
                    "model_config_id": model_id,
                    "skills": [{"id": original.id, "content_version": "1"}],
                },
                "minio-task-message",
            )
        runtime = AgentRuntimeStore(factory, configured)
        claim = runtime.claim_execution(accepted.run.id, 1, prepare_decision)
        assert claim is not None
        with factory() as session:
            activation = activations(session.get(AgentTask, accepted.task.id))[0]["id"]
        runtime.before_send(claim, {"fixture": "no-provider-http"})
        runtime.save_raw(claim, {"fixture": "no-provider-http"})
        assert runtime.save_result(
            claim,
            {
                "output_kind": "tool_requests",
                "output": {
                    "calls": [
                        {
                            "tool_call_id": "read-original-resource",
                            "tool_name": "read_skill_resource",
                            "args": {"activation_id": activation, "path": "references/notes.md"},
                        }
                    ]
                },
                "history": {},
                "usage": {"output_tokens": 1, "output_tokens_reported": True},
            },
        )
        fence = claim.version, claim.token
        operation_id = claim_operation(factory, configured, accepted.run.id, fence=fence)
        assert operation_id is not None
        with factory() as session:
            session.info["actor"] = actor(1)
            library = AgentSkillService(session, configured, storage)
            revised = library.patch(
                original.id,
                {
                    "row_version": "1",
                    "resources": [
                        {"path": "references/notes.md", "content": "# 新资料\n仍保留原版本。"}
                    ],
                },
            )
            assert revised.content_version == 2
            old = library.resource_content(original.id, "1", "references/notes.md")
            new = library.resource_content(original.id, "2", "references/notes.md")
            assert old.content == "# 原资料\n仅供本次测试。"
            assert new.content == "# 新资料\n仍保留原版本。"
            assert old.checksum_sha256 != new.checksum_sha256
            assert len(storage.written) == 2
            library.delete(original.id, {"row_version": str(revised.row_version)})
        complete_operation(factory, configured, accepted.run.id, operation_id, fence=fence)
        with factory() as session:
            operation = session.scalar(
                select(AgentOperation).where(AgentOperation.id == operation_id)
            )
            assert operation.status == "succeeded"
            assert operation.result["content"] == "# 原资料\n仅供本次测试。"
            assert operation.result["checksum_sha256"] == old.checksum_sha256
    finally:
        try:
            for (bucket, key), version in storage.written.items():
                storage.remove(bucket, key, version_id=version)
        finally:
            storage.close()


@pytest.mark.skipif(
    os.environ.get("RUN_AGENT_INFRA_INTEGRATION") != "1",
    reason="Set RUN_AGENT_INFRA_INTEGRATION=1 for configured local MinIO",
)
def test_real_minio_builtin_skill_publication_and_exact_resource_reads(workspace):
    from scripts.agent_system_migration import backfill_skill_packages
    from short_drama.agent.skill.packages import builtin_skill_packages
    from short_drama.service.skills.catalog.builtin_skill import publish_builtin_skills

    factory, _, _ = workspace
    with factory.kw["bind"].begin() as connection:
        backfill_skill_packages(connection)
    configured = Settings(auth_enabled=True, agent_enabled=True)
    storage = TrackedAttachmentStorage(configured)
    packages = builtin_skill_packages()
    try:
        assert len(publish_builtin_skills(factory, configured, storage)["published"]) == 12
        assert len(storage.written) == 25
        assert publish_builtin_skills(factory, configured, storage)["published"] == []
        assert len(storage.written) == 25
        with factory() as session:
            session.info["actor"] = actor(1)
            library = AgentSkillService(session, configured, storage)
            for package in packages:
                detail = library.detail(package.key)
                assert detail.content_version == package.content_version
                for resource in package.content.resources:
                    read = library.resource_content(
                        package.key, str(package.content_version), resource.path
                    )
                    assert read.next_offset is None and read.content == resource.content
                    assert (
                        read.checksum_sha256
                        == hashlib.sha256(resource.content.encode("utf-8")).hexdigest()
                    )
    finally:
        try:
            for (bucket, key), version in storage.written.items():
                assert key.startswith(storage.prefix)
                storage.remove(bucket, key, version_id=version)
        finally:
            storage.close()


@pytest.mark.skipif(
    os.environ.get("RUN_AGENT_INFRA_INTEGRATION") != "1",
    reason="Set RUN_AGENT_INFRA_INTEGRATION=1 for configured local MinIO",
)
def test_real_minio_skill_gc_removes_exact_orphan_version_and_keeps_history(workspace, monkeypatch):
    from datetime import timedelta

    import short_drama.service.skills.resources.skill_resource as resources
    from short_drama.schemas.skills.catalog.skill import SkillCreate
    from short_drama.service.base import utcnow

    factory, _, _ = workspace
    configured = Settings(auth_enabled=True, agent_enabled=True)
    storage = TrackedAttachmentStorage(configured)
    content = {
        "name": "资料回收验证",
        "slug": "gc-resource-test",
        "description": "只核对独立测试对象。",
        "instructions": "读取[资料](references/notes.md)。",
        "resources": [{"path": "references/notes.md", "content": "历史版本必须保留。"}],
    }
    try:
        with factory() as session:
            session.info["actor"] = actor(1)
            library = AgentSkillService(session, configured, storage)
            created = library.create(content, "minio-gc-package")
            orphan = library._store_resources(1, SkillCreate.model_validate(content).resources)[0]
            identity = (orphan["bucket"], orphan["object_name"])
            expected_version = storage.written[identity]
            future = utcnow() + timedelta(days=2)
            monkeypatch.setattr(resources, "database_now", lambda _session: future)
            preview = resources.collect_unreferenced_skill_resources(
                factory, configured, storage, namespace_prefix=storage.prefix
            )
            assert preview["protected"] == 1 and len(preview["candidates"]) == 1
            assert preview["candidates"][0]["version_id"] == (expected_version or "null")
            storage.stat(*identity)
            result = resources.collect_unreferenced_skill_resources(
                factory, configured, storage, apply=True, namespace_prefix=storage.prefix
            )
            assert result["errors"] == [] and len(result["removed"]) == 1
            storage.written.pop(identity)
            with pytest.raises(NotFound):
                storage.stat(*identity)
            assert (
                library.resource_content(created.id, "1", "references/notes.md").content
                == "历史版本必须保留。"
            )
    finally:
        try:
            for (bucket, key), version in storage.written.items():
                assert key.startswith(storage.prefix)
                storage.remove(bucket, key, version_id=version)
        finally:
            storage.close()


def test_missing_context_migration_fails_closed_for_enabled_agent(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        from short_drama.domain.skills.catalog.skill import AgentSkillSetting, AgentSkillVersion

        AgentSkillVersion.__table__.drop(connection)
        AgentSkillSetting.__table__.drop(connection)
        AgentAttachment.__table__.drop(connection)
        AgentSkill.__table__.drop(connection)
    with engine.connect() as connection:
        assert inspect_agent_schema(connection) == {
            "status": "partial",
            "gaps": [
                "agent_attachments",
                "agent_skill_settings",
                "agent_skill_versions",
                "agent_skills",
            ],
        }
    with pytest.raises(RuntimeError, match="Agent schema incomplete"):
        assert_agent_ready(engine, SimpleNamespace(agent_enabled=True, auth_enabled=True))
    assert_identity_ready(engine, SimpleNamespace(agent_enabled=False, auth_enabled=True))


def test_skill_owner_crud_version_freeze_and_disabled_selection(workspace):
    factory, _, _ = workspace
    with factory() as session:
        owner = skills(session)
        created = owner.upload(
            io.BytesIO(
                b"---\nname: tone\ndescription: Quiet tone\n---\n# Original\nUse a quiet tone."
            ),
            "tone.md",
        )
        assert created.content_version == created.row_version == 1
        assert owner.list().total == 1
        assert owner.list(offset=0, limit=1).items[0].id == created.id
        with pytest.raises(NotFound):
            owner.detail("script.v1")
        with pytest.raises(NotFound):
            owner.detail("unknown.v1")
        with session.begin():
            frozen = freeze_skills(session, 1, [SkillSelection(id=created.id, content_version="1")])
        updated = owner.patch(created.id, {"row_version": "1", "instructions": "# Revised"})
        assert updated.content_version == updated.row_version == 2
        assert frozen[0]["instructions"] == "# Original\nUse a quiet tone."
        with pytest.raises(Conflict):
            owner.patch(created.id, {"row_version": "1", "name": "Stale"})
        with session.begin(), pytest.raises(Conflict):
            freeze_skills(session, 1, [SkillSelection(id=created.id, content_version="1")])
        disabled = owner.patch(created.id, {"row_version": "2", "enabled": False})
        with session.begin(), pytest.raises(WorkflowError) as caught:
            freeze_skills(session, 1, [SkillSelection(id=created.id, content_version="2")])
        assert caught.value.code == "agent_skill_disabled"
        owner.delete(created.id, {"row_version": str(disabled.row_version)})
        with pytest.raises(NotFound):
            owner.detail(created.id)
        assert owner.list().total == 0
    with factory() as session:
        member = skills(session, 2)
        assert member.list().total == 0
        with pytest.raises(NotFound):
            member.detail(created.id)
