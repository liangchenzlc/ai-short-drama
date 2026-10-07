"""Real disposable MySQL project chat; provider calls use an explicit in-process double."""

import base64
import io
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from short_drama.agent import model_gateway
from short_drama.agent.runtime import AgentRuntime
from short_drama.agent.tools import execute_tools
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext, token_hash
from short_drama.db.readiness import inspect_agent_schema
from short_drama.domain import AsyncTask, Episode, MediaFile
from short_drama.domain.agent import AgentArtifact, AgentConversation, AgentRun, AgentToolCall
from short_drama.domain.collaboration import ProjectMember, User, UserSession
from short_drama.main import create_app
from short_drama.service.agent_attachment_service import AgentAttachmentService
from short_drama.service.agent_conversation_service import AgentConversationService
from short_drama.service.agent_run_service import AgentRunService
from short_drama.service.agent_skill_service import AgentSkillService
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.assistant_service import AssistantService
from short_drama.service.base import utcnow
from short_drama.service.canvas_service import CanvasService
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_writing_service import EpisodeWritingService
from short_drama.service.project_service import ProjectService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration


def actor(identifier=1):
    return ActorContext(identifier, "creator", "creator@example.test", True, 1, "hash", "test")


@pytest.fixture
def workspace(db_session):
    db_session.info["actor"] = actor()
    project = ProjectService(db_session).create_project({"name": "助手项目", "aspect": "16:9"})
    episode = EpisodeService(db_session).create_for_project(project.id, {"title": "第一集"})
    model = AIModelConfigService(db_session).create(
        {
            "service_type": "text",
            "name": "Fixture",
            "model_key": "fixture",
            "provider": "compatible",
            "base_url": "https://model.invalid/v1",
        }
    )
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    settings = SimpleNamespace(
        agent_enabled=True,
        agent_lease_seconds=180,
        generation_publish_lease_seconds=15,
        encryption_key=None,
    )
    return factory, settings, project.id, episode.id, model.id


def assistant(session, settings, identifier=1):
    session.info["actor"] = actor(identifier)
    return AssistantService(session, settings)


def test_project_scope_resolve_create_archive_and_schema(workspace):
    factory, settings, project, _episode, _model = workspace
    with factory() as session:
        service = assistant(session, settings)
        created = service.create_project_conversation({"project_id": project}, "create")
        assert created.scope_version == 2 and created.episode_id is None
        assert (
            service.create_project_conversation({"project_id": project}, "create").id == created.id
        )
        assert (
            service.create_project_conversation({"project_id": project}, resolve=True).id
            == created.id
        )
        with pytest.raises(Conflict):
            service.create_project_conversation({"project_id": project, "title": "其他"}, "create")
        archive = service.patch_conversation(created.id, {"row_version": 1, "archived": True})
        assert archive.archived
        assert service.list_project_conversations(project).total == 0
        assert service.list_project_conversations(project, include_archived=True).total == 1
        next_conversation = service.create_project_conversation(
            {"project_id": project}, resolve=True
        )
        assert next_conversation.id != created.id
    with factory().get_bind().connect() as connection:
        assert inspect_agent_schema(connection) == {"status": "ready", "gaps": []}


def test_saved_context_is_frozen_and_idempotent_replay_precedes_new_versions(workspace):
    factory, settings, project, episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        conversation = svc.create_project_conversation({"project_id": project}, resolve=True)
        saved = EpisodeWritingService(session).save_novel(
            project, episode, {"content_version": 1, "content": "原正文"}
        )
        payload = {
            "content": "分析节奏",
            "model_config_id": model,
            "context": {
                "kind": "episode",
                "id": str(episode),
                "revision": saved["content_version"],
            },
        }
        accepted = svc.send_message(conversation.id, payload, "message")
        EpisodeWritingService(session).save_novel(
            project, episode, {"content_version": saved["content_version"], "content": "新正文"}
        )
        assert svc.send_message(conversation.id, payload, "message").run.id == accepted.run.id
        with pytest.raises(WorkflowError) as changed:
            svc.send_message(conversation.id, payload, "another")
        assert changed.value.code == "assistant_context_changed"
        with session.begin():
            run = session.get(AgentRun, accepted.run.id)
            assert run.checkpoint["context_snapshot"]["novel"] == "原正文"
            assert run.checkpoint["purpose"] == "assistant_chat"
            assert run.checkpoint["authorization"]["mode"] == "discuss"
            assert run.checkpoint["authorization"]["steps"] == []
            assert run.budget["tool_calls"] == 0
        with pytest.raises(Conflict):
            svc.send_message(conversation.id, {**payload, "content": "不同消息"}, "message")


def test_removed_auto_context_and_missing_manual_reference(workspace):
    factory, settings, project, episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        conversation = svc.create_project_conversation({"project_id": project}, resolve=True)
        accepted = svc.send_message(
            conversation.id,
            {
                "content": "讨论",
                "model_config_id": model,
                "context": {
                    "kind": "episode",
                    "id": str(episode),
                    "revision": "1",
                    "include_document": False,
                },
            },
            "context-off",
        )
        with session.begin():
            source = session.get(AgentRun, accepted.run.id).checkpoint["context_snapshot"]
            assert "novel" not in source and "script" not in source and "shots" not in source
        with pytest.raises(NotFound):
            svc.send_message(
                conversation.id,
                {
                    "content": "讨论",
                    "model_config_id": model,
                    "context": {
                        "kind": "episode",
                        "id": str(episode),
                        "revision": "1",
                        "selected": [{"kind": "shot", "id": str(next_id())}],
                    },
                },
                "missing",
            )
        no_context = svc.send_message(
            conversation.id, {"content": "纯讨论", "model_config_id": model}, "none"
        )
        with session.begin():
            assert session.get(AgentRun, no_context.run.id).checkpoint["context_snapshot"] is None


def test_legacy_api_cannot_write_project_chat_and_new_api_cannot_write_legacy(workspace):
    factory, settings, project, episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        current = svc.create_project_conversation({"project_id": project}, resolve=True)
        legacy = AgentConversationService(session, settings).create_conversation(
            {
                "project_id": project,
                "episode_id": episode,
                "stage": "source",
                "subject_type": "episode",
                "subject_id": episode,
                "task_type": "writing",
            }
        )
        assert svc.list_project_conversations(project).total == 1
        assert svc.list_project_conversations(project, legacy=True).items[0].id == legacy.id
        with pytest.raises(WorkflowError) as old_write:
            AgentRunService(session, settings).send_message(
                current.id, {"content": "生成", "mode": "generate", "model_config_id": model}, "old"
            )
        assert old_write.value.code == "assistant_api_required"
        with pytest.raises(WorkflowError) as new_write:
            svc.send_message(legacy.id, {"content": "讨论", "model_config_id": model}, "new")
        assert new_write.value.code == "assistant_legacy_readonly"
        with pytest.raises(WorkflowError):
            AgentConversationService(session, settings).require_writable_conversation(current.id)


def test_project_members_never_read_other_users_chats_and_revocation_ends_access(workspace):
    factory, settings, project, _episode, _model = workspace
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="assistant_member",
                display_name="Member",
                email="member@example.test",
                password_hash="test",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
        session.flush()
        member_id = next_id()
        session.add(
            ProjectMember(
                id=member_id, project_id=project, user_id=2, status="active", joined_at=utcnow()
            )
        )
    with factory() as session:
        private = assistant(session, settings).create_project_conversation({"project_id": project})
        member = assistant(session, settings, 2)
        assert member.list_project_conversations(project).total == 0
        with pytest.raises(NotFound):
            member.get_conversation(private.id)
        own = member.create_project_conversation({"project_id": project})
    with factory.begin() as session:
        session.get(ProjectMember, member_id).status = "removed"
    with factory() as session:
        with pytest.raises(NotFound):
            assistant(session, settings, 2).get_conversation(own.id)


def test_text_attachment_and_personal_skill_are_frozen_without_tools(workspace):
    factory, settings, project, _episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        conversation = svc.create_project_conversation({"project_id": project})
        attachment = AgentAttachmentService(session, settings).upload(
            conversation.id, io.BytesIO("人物设定".encode()), "reference.md", "upload"
        )
        skill = AgentSkillService(session, settings).upload(
            io.BytesIO("# 提供节奏建议".encode()), "review.md"
        )
        accepted = svc.send_message(
            conversation.id,
            {
                "content": "分析",
                "model_config_id": model,
                "attachment_ids": [attachment.id],
                "skills": [{"id": skill.id, "content_version": skill.content_version}],
            },
            "with-inputs",
        )
        with session.begin():
            run = session.get(AgentRun, accepted.run.id)
            assert run.checkpoint["selected_skills"][0]["instructions"] == "# 提供节奏建议"
            assert run.checkpoint["user_prompt"]["attachments"][0]["text_content"] == "人物设定"
            assert run.checkpoint["purpose"] == "assistant_chat"


class ChatGateway:
    def __init__(self, tools=False):
        self.tools = tools
        self.calls = 0

    async def run_segment(
        self, snapshot, credential, *, on_request, on_response, on_text_delta, **kwargs
    ):
        assert kwargs["tools"] == [] and kwargs["max_tool_calls"] == 0
        await on_request({"codec": "agent.request", "body": {"stream": True}})
        self.calls += 1
        await on_response(
            {"codec": "agent.raw-response", "body_b64": base64.b64encode(b"fixture").decode()}
        )
        await on_text_delta("这是建议")
        return {
            "output_kind": "tool_requests" if self.tools else "text",
            "output": {"calls": [{"tool_call_id": "bad", "tool_name": "prepare_task", "args": {}}]}
            if self.tools
            else "这是建议",
            "history": {},
            "usage": {"output_tokens": 5, "output_tokens_reported": True},
        }


@pytest.mark.parametrize("tools", [False, True])
def test_worker_text_or_forbidden_tool_reply_never_creates_creative_effects(
    workspace, monkeypatch, tools
):
    factory, settings, project, episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        conversation = svc.create_project_conversation({"project_id": project})
        first = svc.send_message(
            conversation.id, {"content": "请生成图片", "model_config_id": model}, "one"
        )
        second = svc.send_message(
            conversation.id, {"content": "只讨论", "model_config_id": model}, "two"
        )
        assert second.run.queue_position == 1
    monkeypatch.setattr(model_gateway, "serialize_segment_result", lambda result: deepcopy(result))
    gateway = ChatGateway(tools)
    runtime = AgentRuntime(factory, settings, gateway)
    runtime.execute_one(first.run.id, 1)
    assert gateway.calls == 1
    with factory() as session:
        svc = assistant(session, settings)
        run = svc.get_run(first.run.id)
        assert run.status == ("failed" if tools else "succeeded")
        if tools:
            assert run.error["code"] == "assistant_tools_forbidden"
        state = svc.runtime_state(conversation.id)
        assert state.queued_runs[0].queue_position == 0
        assert svc.stop(second.run.id).status == "cancelled"
        with session.begin():
            for entity in (AgentToolCall, AgentArtifact, AsyncTask):
                assert session.scalar(select(func.count()).select_from(entity)) == 0
            assert session.get(Episode, episode).content_version == 1


def test_corrupted_tool_phase_is_rejected_before_any_tool_execution(workspace):
    factory, settings, project, _episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        conversation = svc.create_project_conversation({"project_id": project})
        message = svc.send_message(
            conversation.id, {"content": "分析", "model_config_id": model}, "bad-phase"
        )
    with factory.begin() as session:
        session.get(AgentRun, message.run.id).phase = "tools"
    execute_tools(factory, settings, message.run.id)
    with factory() as session:
        assert (
            assistant(session, settings).get_run(message.run.id).error["code"]
            == "assistant_tools_forbidden"
        )


def test_canvas_context_is_project_bound_versioned_and_manual_node_missing_fails(workspace):
    factory, settings, project, _episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        other = ProjectService(session).create_project(
            {"name": "画布项目", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
            idempotency_key="assistant-canvas-project",
        )
        canvas = CanvasService(session).list_for_project(other.id)["items"][0]
        source_key = canvas.source_key if hasattr(canvas, "source_key") else canvas["source_key"]
        version = canvas.row_version if hasattr(canvas, "row_version") else canvas["row_version"]
        conversation = svc.create_project_conversation({"project_id": other.id})
        context = {"kind": "canvas", "id": source_key, "revision": str(version)}
        accepted = svc.send_message(
            conversation.id,
            {"content": "分析画布", "model_config_id": model, "context": context},
            "canvas",
        )
        with session.begin():
            assert (
                session.get(AgentRun, accepted.run.id).checkpoint["context_snapshot"]["kind"]
                == "canvas"
            )
        with pytest.raises(NotFound):
            svc.send_message(
                conversation.id,
                {
                    "content": "分析",
                    "model_config_id": model,
                    "context": {**context, "selected": [{"kind": "node", "id": "missing"}]},
                },
                "missing-node",
            )
        wrong = svc.create_project_conversation({"project_id": project})
        with pytest.raises(NotFound):
            svc.send_message(
                wrong.id,
                {"content": "越界", "model_config_id": model, "context": context},
                "cross-project",
            )


def canvas_media_context(session):
    project = ProjectService(session).create_project(
        {"name": "媒体画布", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
        idempotency_key="assistant-media-canvas",
    )
    media_id = next_id()
    with session.begin():
        session.add(
            MediaFile(
                id=media_id,
                project_id=project.id,
                created_by=1,
                format_code="image/png",
                storage_locator=f"minio://fixture/{media_id}",
                original_name="selected.png",
                byte_size=100,
                checksum_sha256="a" * 64,
                width=16,
                height=12,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    canvas_service = CanvasService(session)
    document = canvas_service.read(project.id, project.primary_canvas_id, private=True)[
        "source_document"
    ]
    document["nodes"] = [
        {
            "id": "chosen",
            "type": "image",
            "title": "参考图片",
            "position": {"x": 0, "y": 0},
            "width": 100,
            "height": 100,
            "metadata": {"storageKey": f"resource:{media_id}"},
        }
    ]
    canvas_service.commit(
        project.id,
        project.primary_canvas_id,
        {"expected_row_version": document["revision"], "source_document": document},
        "assistant-media-graph",
    )
    document = canvas_service.read(project.id, project.primary_canvas_id, private=True)[
        "source_document"
    ]
    context = {
        "kind": "canvas",
        "id": document["id"],
        "revision": document["revision"],
        "selected": [{"kind": "node", "id": "chosen"}],
    }
    return project.id, media_id, context


def test_explicit_canvas_media_is_frozen_but_auto_context_does_not_attach_pixels(workspace):
    factory, settings, _project, _episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        project, media_id, context = canvas_media_context(session)
        conversation = svc.create_project_conversation({"project_id": project})
        auto = svc.send_message(
            conversation.id,
            {
                "content": "分析文字",
                "model_config_id": model,
                "context": {**context, "selected": []},
            },
            "auto",
        )
        selected = svc.send_message(
            conversation.id,
            {"content": "分析图片", "model_config_id": model, "context": context},
            "selected",
        )
        with session.begin():
            assert isinstance(session.get(AgentRun, auto.run.id).checkpoint["user_prompt"], str)
            attachment = session.get(AgentRun, selected.run.id).checkpoint["user_prompt"][
                "attachments"
            ][0]
            assert attachment["media_id"] == str(media_id)
            assert attachment["storage_locator"] == f"minio://fixture/{media_id}"
            assert attachment["checksum_sha256"] == "a" * 64
        assert selected.message.references[0]["media"][0]["media_id"] == str(media_id)


def test_canvas_reference_cannot_read_another_members_unpublished_media(workspace):
    factory, settings, _project, _episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        project, media_id, context = canvas_media_context(session)
        conversation = svc.create_project_conversation({"project_id": project})
    with factory.begin() as session:
        media = session.get(MediaFile, media_id)
        media.created_by, media.published_at = 2, None
    with factory() as session:
        with pytest.raises(NotFound):
            assistant(session, settings).send_message(
                conversation.id,
                {"content": "不可读媒体", "model_config_id": model, "context": context},
                "private-media",
            )


def test_selected_media_and_uploads_share_the_sixteen_input_limit(workspace):
    factory, settings, _project, _episode, model = workspace
    with factory() as session:
        svc = assistant(session, settings)
        project, _media_id, context = canvas_media_context(session)
        conversation = svc.create_project_conversation({"project_id": project})
        uploads = AgentAttachmentService(session, settings)
        attachment_ids = [
            uploads.upload(
                conversation.id, io.BytesIO(b"reference"), f"{index}.txt", f"input-{index}"
            ).id
            for index in range(16)
        ]
        with pytest.raises(WorkflowError) as limited:
            svc.send_message(
                conversation.id,
                {
                    "content": "分析",
                    "model_config_id": model,
                    "context": context,
                    "attachment_ids": attachment_ids,
                },
                "too-many",
            )
        assert limited.value.code == "agent_context_too_large"
        assert svc.list_messages(conversation.id).total == 0


def test_real_http_message_state_events_and_member_privacy(workspace, monkeypatch):
    factory, _settings, project, _episode, model = workspace
    cfg = Settings(
        _env_file=None,
        auth_enabled=True,
        agent_enabled=True,
        auth_cookie_secure=False,
        public_origin="http://testserver",
    )
    app = create_app(cfg)
    app.state.session_factory, app.state.agent_schema_ready = factory, True
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="http_assistant_member",
                display_name="Member",
                email="http_member@example.test",
                password_hash="test",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
        session.flush()
        session.add(
            ProjectMember(
                id=next_id(), project_id=project, user_id=2, status="active", joined_at=utcnow()
            )
        )

    def client(identifier):
        token, csrf = f"assistant-http-{identifier}", f"assistant-csrf-{identifier}"
        with factory.begin() as session:
            session.add(
                UserSession(
                    id=next_id(),
                    user_id=identifier,
                    token_hash=token_hash(token),
                    csrf_hash=token_hash(csrf),
                    created_at=utcnow(),
                    expires_at=utcnow() + timedelta(days=1),
                )
            )
        result = TestClient(app)
        result.cookies.set("sd_session", token)
        result.headers.update({"Origin": cfg.public_origin, "X-CSRF-Token": csrf})
        return result

    owner, member = client(1), client(2)
    created = owner.post(
        "/api/v1/assistant/conversations",
        json={"project_id": str(project)},
        headers={"Idempotency-Key": "http-create"},
    )
    assert created.status_code == 201, created.text
    conversation = created.json()["id"]
    path = f"/api/v1/assistant/conversations/{conversation}"
    body = {"content": "分析", "model_config_id": str(model)}
    accepted = owner.post(
        path + "/messages", json=body, headers={"Idempotency-Key": "http-message"}
    )
    assert accepted.status_code == 201, accepted.text
    run = accepted.json()["run"]["id"]
    assert (
        owner.post(
            path + "/messages", json=body, headers={"Idempotency-Key": "http-message"}
        ).json()["run"]["id"]
        == run
    )
    state = owner.get(path + "/state").json()
    assert state["active_run"] is None and state["queued_runs"][0]["id"] == run
    for suffix in ("", "/messages", "/state", "/events"):
        assert member.get(path + suffix).status_code == 404
    assert (
        member.get("/api/v1/assistant/conversations", params={"project_id": str(project)}).json()[
            "total"
        ]
        == 0
    )

    # Old write routes require V1, while the new namespace retains shared services.
    old = f"/api/v1/agent/conversations/{conversation}"
    denied = [
        owner.patch(old, json={"row_version": "1", "title": "旧入口不得改写"}),
        owner.post(old + "/messages", json=body, headers={"Idempotency-Key": "old-message"}),
        owner.post(
            old + "/attachments/uploads",
            files={"file": ("old.txt", b"reference", "text/plain")},
            headers={"Idempotency-Key": "old-upload"},
        ),
        owner.post(
            old + "/attachments/references",
            json={"source_type": "media", "source_id": "1"},
            headers={"Idempotency-Key": "old-reference"},
        ),
        owner.delete(old + "/attachments/1"),
        owner.post(f"/api/v1/agent/runs/{run}/stop"),
        owner.post(
            f"/api/v1/agent/runs/{run}/reviews/1",
            json={"review_version": "1", "review_hash": "a" * 64, "decision": "approved"},
        ),
        owner.post(
            f"/api/v1/agent/runs/{run}/continue",
            json={"artifact_id": "1", "artifact_row_version": "1"},
        ),
    ]
    assert all(response.status_code == 409 for response in denied), [
        (response.status_code, response.text) for response in denied
    ]
    assert owner.get(path).json()["title"] == "新对话"
    assert owner.get(path + "/messages").json()["total"] == 1
    upload = owner.post(
        path + "/attachments/uploads",
        files={"file": ("current.txt", b"reference", "text/plain")},
        headers={"Idempotency-Key": "new-upload"},
    )
    assert upload.status_code == 201, upload.text
    assert owner.delete(path + "/attachments/" + upload.json()["id"]).status_code == 204

    # Terminate this HTTP stream after its real initial MySQL event batch.
    async def disconnect_after_batch(request):
        calls = getattr(request.state, "test_stream_polls", 0)
        request.state.test_stream_polls = calls + 1
        return calls > 0

    monkeypatch.setattr(Request, "is_disconnected", disconnect_after_batch)
    stream = owner.get(path + "/events")
    assert stream.status_code == 200
    assert "event: agent" in stream.text and "message.created" in stream.text
    assert "run.started" in stream.text
    assert stream.headers["cache-control"] == "no-store"
    monkeypatch.setattr(model_gateway, "serialize_segment_result", lambda result: deepcopy(result))
    gateway = ChatGateway()
    AgentRuntime(factory, cfg, gateway).execute_one(int(run), 1)
    assert gateway.calls == 1
    assert owner.get("/api/v1/assistant/runs/" + run).json()["status"] == "succeeded"
    assert owner.get(path + "/messages").json()["items"][-1]["content"] == "这是建议"


def test_project_chat_incremental_sql_retains_legacy_conversation_and_enables_v2(
    migration_mysql_engine,
):
    engine = migration_mysql_engine
    cfg = SimpleNamespace(agent_enabled=True)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        session.info["actor"] = actor()
        project = ProjectService(session).create_project({"name": "旧对话", "aspect": "16:9"})
        episode = EpisodeService(session).create_for_project(project.id, {"title": "旧分集"})
        legacy = AgentConversationService(session, cfg).create_conversation(
            {
                "project_id": project.id,
                "episode_id": episode.id,
                "stage": "source",
                "subject_type": "episode",
                "subject_id": episode.id,
                "task_type": "writing",
            }
        )
    check = next(
        constraint
        for constraint in AgentConversation.__table__.constraints
        if constraint.name == "ck_agent_conversations_scope"
    )
    old_check = str(check.sqltext).split(" OR ", 1)[1]
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE agent_conversations DROP FOREIGN KEY fk_agent_conversations_episode"
        )
        connection.exec_driver_sql(
            "ALTER TABLE agent_conversations DROP CHECK ck_agent_conversations_scope, "
            "DROP INDEX idx_agent_conversations_project_chat, "
            "MODIFY COLUMN episode_id BIGINT UNSIGNED NOT NULL, "
            "ADD CONSTRAINT ck_agent_conversations_scope CHECK (" + old_check + ")"
        )
        connection.exec_driver_sql(
            "ALTER TABLE agent_conversations ADD CONSTRAINT fk_agent_conversations_episode "
            "FOREIGN KEY (episode_id) REFERENCES episodes (id) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT"
        )
        assert inspect_agent_schema(connection)["status"] == "partial"
        sql = (
            Path(__file__).resolve().parents[3]
            / "docs"
            / "数据库模型"
            / "migrations"
            / "2026-10-06-project-assistant"
            / "001-project-assistant.sql"
        )
        connection.exec_driver_sql(sql.read_text(encoding="utf-8").strip().removesuffix(";"))
        assert inspect_agent_schema(connection) == {"status": "ready", "gaps": []}
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        service = assistant(session, cfg)
        assert service.list_project_conversations(project.id, legacy=True).items[0].id == legacy.id
        current = service.create_project_conversation({"project_id": project.id})
        assert current.scope_version == 2 and current.episode_id is None
