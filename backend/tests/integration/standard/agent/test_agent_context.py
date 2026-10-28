"""Disposable MySQL context ownership; real MinIO is opt-in, models are never invoked."""

import base64
import hashlib
import io
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from PIL import Image

import tests.integration.standard.agent.test_agent_conversations as conversation_tests
from short_drama.agent.context.media import materialize_prompt
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.db.readiness import assert_agent_ready, assert_identity_ready, inspect_agent_schema
from short_drama.domain import Asset, MediaFile
from short_drama.domain.agent import AgentMessage, AgentRun
from short_drama.domain.agent_context import AgentAttachment, AgentSkill
from short_drama.schemas.agent_context import SkillSelection
from short_drama.service.agent_attachment_service import AgentAttachmentService
from short_drama.service.agent_model_service import AgentModelService
from short_drama.service.agent_run_service import AgentRunService
from short_drama.service.agent_skill_service import AgentSkillService, freeze_skills
from short_drama.service.project_service import ProjectService
from short_drama.service.storage_service import StorageService
from short_drama.storage.minio import MinioStorage
from short_drama.utils.snowflake import next_id
from tests.integration.standard.agent.test_agent_conversations import actor, service
from tests.integration.standard.agent.test_agent_services import send, settings, setup

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
def test_real_minio_attachment_upload_read_message_freeze_and_exact_version_cleanup(workspace):
    factory, project_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id)
    configured = Settings(auth_enabled=True, agent_enabled=True)
    storage = TrackedAttachmentStorage(configured)
    managed = StorageService(storage, configured)
    image = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(image, "PNG")
    data = image.getvalue()
    try:
        with factory() as session:
            session.info["actor"] = actor(1)
            library = AgentAttachmentService(session, configured, storage)
            uploaded = library.upload(
                conversation_id, io.BytesIO(data), "context.png", "real-minio-upload"
            )
            replay = library.upload(
                conversation_id, io.BytesIO(data), "context.png", "real-minio-upload"
            )
            assert uploaded.id == replay.id and uploaded.pending
            assert len(storage.written) == 1
            assert uploaded.checksum_sha256 == hashlib.sha256(data).hexdigest()
            AgentModelService(session, configured).patch_inputs(
                model_id, {"row_version": 1, "image": True, "audio": False}
            )
            accepted = AgentRunService(session, configured, storage).send_message(
                conversation_id,
                {
                    "content": "Review this local image",
                    "model_config_id": model_id,
                    "attachment_ids": [uploaded.id],
                },
                "real-minio-message",
            )
            assert accepted.run.status == "queued"
            assert accepted.message.references[0]["url"]
            assert library.list(conversation_id, pending=True).total == 0
            library.delete(conversation_id, uploaded.id)
            messages = AgentRunService(session, configured, storage).list_messages(conversation_id)
            assert messages.items[0].references[0]["url"]
        with factory.begin() as session:
            media = session.get(MediaFile, uploaded.media_id)
            locator = media.storage_locator
            assert media.scope_user_id == 1 and media.project_id is None
            assert media.published_at is None
            persisted = session.get(AgentMessage, accepted.message.id)
            assert "url" not in persisted.references[0]
            run = session.get(AgentRun, accepted.run.id)
            frozen = run.checkpoint["user_prompt"]
            assert frozen["attachments"][0]["storage_locator"] == locator
            assert "url" not in frozen["attachments"][0]
            assert "https://" not in json.dumps(run.checkpoint)
            assert session.get(AgentAttachment, uploaded.id).deleted_at is not None
        assert managed.stat(locator).size == len(data)
        with managed.open(locator) as response:
            assert response.read() == data
        downloaded = httpx.get(uploaded.url, timeout=10, trust_env=False)
        assert downloaded.status_code == 200 and downloaded.content == data
        parts = materialize_prompt(frozen, configured, managed)
        assert parts[-1]["media_type"] == "image/jpeg"
        with Image.open(io.BytesIO(base64.urlsafe_b64decode(parts[-1]["data"]))) as decoded:
            assert decoded.size == (24, 16) and decoded.mode == "RGB"
        with factory() as session:
            session.info["actor"] = actor(2)
            with pytest.raises(NotFound):
                AgentAttachmentService(session, configured, storage).list(conversation_id)
    finally:
        try:
            for (bucket, key), version in storage.written.items():
                assert key.startswith(storage.prefix)
                if version is None:
                    try:
                        version = storage.stat(bucket, key).version_id
                    except NotFound:
                        continue
                storage.remove(bucket, key, version)
        finally:
            storage.close()


@pytest.mark.skipif(
    os.environ.get("RUN_AGENT_INFRA_INTEGRATION") != "1",
    reason="Set RUN_AGENT_INFRA_INTEGRATION=1 for configured local MinIO",
)
def test_real_minio_skill_package_versions_keep_exact_resources(workspace):
    """Exercise immutable resources through the explicitly frozen legacy V2 engine."""
    from sqlalchemy import select

    from short_drama.agent.coordinator.assistant import claim_operation, complete_operation
    from short_drama.agent.coordinator.runtime import AgentRuntimeStore
    from short_drama.agent.legacy.tools import prepare_decision
    from short_drama.agent.skill.contracts import activations
    from short_drama.domain.agent_task import AgentOperation, AgentTask
    from short_drama.service.assistant_task_service import AssistantTaskService
    from tests.integration.standard.assistant.test_global_assistant_tasks import legacy_task_engine

    factory, project_id, _ = workspace
    _, model_id = setup(factory, project_id)
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
    from short_drama.service.agent_builtin_skill_service import publish_builtin_skills

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

    from short_drama.schemas.agent_skill import SkillCreate
    from short_drama.service import agent_skill_resource_service as resources
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
        from short_drama.domain.agent_skill import AgentSkillSetting, AgentSkillVersion

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
        created = owner.upload(io.BytesIO(b"# Original\nUse a quiet tone."), "tone.md")
        assert created.content_version == created.row_version == 1
        assert owner.list().total == 9
        assert owner.list(offset=8, limit=1).items[0].id == created.id
        assert owner.detail("script.v1").builtin
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
        assert owner.list().total == 8
    with factory() as session:
        member = skills(session, 2)
        assert member.list().total == 8
        with pytest.raises(NotFound):
            member.detail(created.id)


def test_text_attachment_idempotency_pending_and_accepted_context_survive_edits(workspace):
    factory, project_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id)
    with factory() as session:
        library = attachments(session)
        uploaded = library.upload(
            conversation_id, io.BytesIO("资料正文".encode()), "notes.md", "input-1"
        )
        replay = library.upload(
            conversation_id, io.BytesIO("资料正文".encode()), "notes.md", "input-1"
        )
        assert uploaded.id == replay.id and uploaded.pending
        with pytest.raises(Conflict):
            library.upload(conversation_id, io.BytesIO(b"Other"), "notes.md", "input-1")
        assert library.list(conversation_id, pending=True).total == 1
        custom = skills(session).upload(
            io.BytesIO(b"# Loaded\nKeep dialogue concise."), "dialogue.md"
        )
        other = service(session).create_conversation({"project_id": project_id})
    with pytest.raises(NotFound):
        send(factory, other.id, model_id, attachment_ids=[uploaded.id])
    accepted = send(
        factory,
        conversation_id,
        model_id,
        attachment_ids=[uploaded.id],
        skills=[{"id": custom.id, "content_version": "1"}],
    )
    with factory() as session:
        library = attachments(session)
        assert library.list(conversation_id, pending=True).total == 0
        library.delete(conversation_id, uploaded.id)
        skills(session).patch(custom.id, {"row_version": "1", "instructions": "# Changed"})
        skills(session).delete(custom.id, {"row_version": "2"})
    replay = send(
        factory,
        conversation_id,
        model_id,
        attachment_ids=[uploaded.id],
        skills=[{"id": custom.id, "content_version": "1"}],
    )
    assert replay.run.id == accepted.run.id
    with factory.begin() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.checkpoint["user_prompt"]["attachments"][0]["text_content"] == "资料正文"
        assert (
            run.checkpoint["selected_skills"][0]["instructions"]
            == "# Loaded\nKeep dialogue concise."
        )
        assert "https://" not in json.dumps(run.checkpoint)
        assert session.get(AgentAttachment, uploaded.id).attached_message_id == accepted.message.id
    with factory() as session:
        with pytest.raises(NotFound):
            attachments(session, 2).list(conversation_id)


def test_asset_media_reference_remains_private_and_model_declaration_preserves_tools(workspace):
    factory, project_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id)
    with factory.begin() as session:
        media = MediaFile(
            id=next_id(),
            project_id=project_id,
            format_code="image/png",
            storage_locator=f"minio://{settings().minio_image_bucket}/private.png",
            created_by=1,
            original_name="private.png",
            byte_size=12,
            checksum_sha256="a" * 64,
        )
        session.add(media)
        session.flush()
        asset = Asset(
            id=next_id(),
            project_id=project_id,
            position=1,
            kind="character",
            name="Private character",
            description="Profile",
            prompt="Portrait",
            media_id=media.id,
        )
        session.add(asset)
        session.flush()
        media_id, asset_id = media.id, asset.id
    with factory() as session:
        library = attachments(session)
        referenced = library.reference(
            conversation_id, {"source_type": "asset", "source_id": asset_id}, "reference-1"
        )
        assert referenced.media_id == media_id and referenced.kind == "image"
        assert referenced.metadata["source_id"] == str(asset_id)
        configured = AgentModelService(session, settings()).patch_inputs(
            model_id,
            {
                "row_version": 1,
                "image": True,
                "audio": False,
            },
        )
        assert configured.verified and configured.input_capabilities.evidence == "declared"
        accepted = AgentRunService(session, settings(), library.storage).send_message(
            conversation_id,
            {"content": "Review", "model_config_id": model_id, "attachment_ids": [referenced.id]},
            "reference-message",
        )
        assert accepted.message.references[0]["url"].startswith("https://local.invalid/")
    with factory.begin() as session:
        media = session.get(MediaFile, media_id)
        assert media.scope_user_id is None and media.project_id == project_id
        run = session.get(AgentRun, accepted.run.id)
        frozen = run.checkpoint["user_prompt"]["attachments"][0]
        assert "Portrait" in frozen["text_content"] and frozen["storage_locator"].startswith(
            "minio://"
        )
        assert "url" not in frozen
    with factory() as session:
        session.info["actor"] = actor(2)
        other_project = ProjectService(session).create({"name": "独立账号的作品"})
        member_conversation = service(session, 2).create_conversation(
            {"project_id": other_project.id}
        )
        member = attachments(session, 2)
        with pytest.raises(NotFound):
            member.reference(
                member_conversation.id,
                {"source_type": "media", "source_id": media_id},
                "attack-media",
            )
        with pytest.raises(NotFound):
            member.reference(
                member_conversation.id,
                {"source_type": "asset", "source_id": asset_id},
                "attack-asset",
            )
        with pytest.raises(NotFound):
            member.delete(conversation_id, referenced.id)
    with factory() as session:
        with pytest.raises(Conflict):
            attachments(session).reference(
                conversation_id, {"source_type": "media", "source_id": media_id}, "reference-1"
            )
