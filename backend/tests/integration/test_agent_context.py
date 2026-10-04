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
import test_agent_conversations as conversation_tests
from PIL import Image
from test_agent_conversations import actor, service
from test_agent_services import send, settings, setup

from short_drama.agent.input_media import materialize_prompt
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
from short_drama.service.storage_service import StorageService
from short_drama.storage.minio import MinioStorage
from short_drama.utils.snowflake import next_id

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
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
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


def test_missing_context_migration_fails_closed_for_enabled_agent(migration_mysql_engine):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        AgentAttachment.__table__.drop(connection)
        AgentSkill.__table__.drop(connection)
    with engine.connect() as connection:
        assert inspect_agent_schema(connection) == {
            "status": "partial",
            "gaps": ["agent_attachments", "agent_skills"],
        }
    with pytest.raises(RuntimeError, match="Agent schema incomplete"):
        assert_agent_ready(engine, SimpleNamespace(agent_enabled=True, auth_enabled=True))
    assert_identity_ready(engine, SimpleNamespace(agent_enabled=False, auth_enabled=True))


def test_skill_owner_crud_version_freeze_and_disabled_selection(workspace):
    factory, _, _, _ = workspace
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
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
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
        other = service(session).create_conversation(
            {"project_id": project_id, "episode_id": episode_id}
        )
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
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = setup(factory, project_id, episode_id)
    with factory.begin() as session:
        media = MediaFile(
            id=next_id(),
            scope_user_id=1,
            project_id=None,
            format_code="image/png",
            storage_locator=f"minio://{settings().minio_image_bucket}/private.png",
            original_name="private.png",
            byte_size=12,
            checksum_sha256="a" * 64,
        )
        session.add(media)
        session.flush()
        asset = Asset(
            id=next_id(),
            scope_user_id=1,
            project_id=None,
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
        assert media.scope_user_id == 1 and media.project_id is None
        run = session.get(AgentRun, accepted.run.id)
        frozen = run.checkpoint["user_prompt"]["attachments"][0]
        assert "Portrait" in frozen["text_content"] and frozen["storage_locator"].startswith(
            "minio://"
        )
        assert "url" not in frozen
    with factory() as session:
        member_conversation = service(session, 2).create_conversation(
            {"project_id": project_id, "episode_id": episode_id}
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
