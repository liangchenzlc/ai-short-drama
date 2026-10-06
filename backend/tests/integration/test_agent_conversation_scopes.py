"""随机 MySQL 中验证对象会话隔离、首次进入串行解析和历史兼容。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
import test_agent_artifacts as artifact_tests
import test_agent_conversations as conversation_tests
import test_agent_services as run_tests
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import token_hash
from short_drama.domain import Asset, EpisodeAsset, ProjectAsset, ShotScript
from short_drama.domain.agent import AgentConversation, AgentMessage, AgentRun
from short_drama.domain.collaboration import UserSession
from short_drama.main import create_app
from short_drama.schemas.agent import ConversationScope
from short_drama.service.episode_service import EpisodeService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace
service = conversation_tests.service


def subjects(workspace):
    factory, project_id, episode_id, _ = workspace
    with factory() as session:
        service(session)
        other = EpisodeService(session).create_for_project(project_id, {"title": "Other episode"})
    identifiers = [next_id() for _ in range(3)]
    with factory.begin() as session:
        for identifier in identifiers:
            session.add(
                Asset(id=identifier, project_id=project_id, kind="character", name="同名人物")
            )
        session.flush()
        for position, identifier in enumerate(identifiers, 1):
            session.add(
                ProjectAsset(
                    id=next_id(), project_id=project_id, asset_id=identifier, position=position
                )
            )
            session.add(
                EpisodeAsset(
                    id=next_id(),
                    episode_id=episode_id if position < 3 else other.id,
                    asset_id=identifier,
                    position=position,
                )
            )
        shot_id = next_id()
        session.add(ShotScript(id=shot_id, episode_id=episode_id, position=1, script="A shot"))
    return identifiers, shot_id, other.id


def payload(workspace, subject_id, *, stage="assets", subject_type="asset", task_type="creation"):
    _, project_id, episode_id, _ = workspace
    return {
        "project_id": project_id,
        "episode_id": episode_id,
        "title": "同名人物的对话",
        "stage": stage,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "task_type": task_type,
    }


def scope(values, *, task=True):
    return ConversationScope.model_validate(
        {
            name: values[name]
            for name in ("stage", "subject_type", "subject_id", "task_type")
            if name != "task_type" or task
        }
    )


def test_same_name_subjects_tasks_owners_and_legacy_remain_isolated(workspace):
    factory, project_id, episode_id, _ = workspace
    identifiers, shot_id, _ = subjects(workspace)
    first = payload(workspace, identifiers[0])
    with factory() as session:
        svc = service(session)
        original = svc.resolve_conversation(first)
        assert svc.resolve_conversation(first).id == original.id
        image = svc.create_conversation({**first, "task_type": "image"})
        svc.create_conversation(payload(workspace, identifiers[1]))
        svc.create_conversation(
            payload(workspace, shot_id, stage="storyboard", subject_type="shot")
        )
        legacy = svc.create_conversation(
            {"project_id": project_id, "episode_id": episode_id, "title": first["title"]}
        )
        assert legacy.scope_version == 0 and legacy.subject_id is None
        page = svc.list_conversations(project_id, episode_id, scope=scope(first))
        assert page.total == 1 and page.items[0].id == original.id
        all_tasks = svc.list_conversations(project_id, episode_id, scope=scope(first, task=False))
        assert all_tasks.total == 2 and {row.id for row in all_tasks.items} == {
            original.id,
            image.id,
        }
        old = svc.list_conversations(project_id, episode_id)
        assert old.total == 1 and old.items[0].id == legacy.id
        assert (
            svc.list_conversations(
                project_id, episode_id, scope=scope(first, task=False), search="不存在"
            ).total
            == 0
        )
    with factory.begin() as session:
        session.add(
            AgentMessage(
                id=next_id(),
                conversation_id=original.id,
                seq=1,
                role="user",
                content="当前人物" * 60,
            )
        )
        session.add(
            AgentMessage(
                id=next_id(), conversation_id=image.id, seq=1, role="user", content="图片任务消息"
            )
        )
    with factory() as session:
        svc = service(session)
        page = svc.list_conversations(project_id, episode_id, scope=scope(first, task=False))
        previews = {row.id: row.last_message_preview for row in page.items}
        assert previews == {original.id: ("当前人物" * 60)[:200], image.id: "图片任务消息"}
        assert (
            svc.get_conversation(original.id, scope(first)).last_message_preview
            == previews[original.id]
        )
    with factory() as session:
        member = service(session, 2)
        assert member.list_conversations(project_id, episode_id, scope=scope(first)).total == 0
        with pytest.raises(NotFound):
            member.get_conversation(original.id, scope(first))
        assert member.resolve_conversation(first).id != original.id


def test_scope_mismatch_and_unlinked_or_deleted_objects_do_not_resolve(workspace):
    factory, _, _, _ = workspace
    identifiers, shot_id, _ = subjects(workspace)
    first = payload(workspace, identifiers[0])
    with factory() as session:
        svc = service(session)
        original = svc.resolve_conversation(first)
        with pytest.raises(WorkflowError) as mismatched:
            svc.get_conversation(original.id, scope(payload(workspace, identifiers[1])))
        assert mismatched.value.code == "agent_scope_mismatch"
        with pytest.raises(WorkflowError) as unlinked:
            svc.resolve_conversation(payload(workspace, identifiers[2]))
        assert unlinked.value.code == "agent_subject_unavailable"
    with factory.begin() as session:
        session.execute(delete(EpisodeAsset).where(EpisodeAsset.asset_id == identifiers[0]))
        session.get(ShotScript, shot_id).deleted_at = conversation_tests.utcnow()
    with factory() as session:
        svc = service(session)
        assert svc.get_conversation(original.id, scope(first)).id == original.id
        with pytest.raises(WorkflowError):
            svc.resolve_conversation(first)
        with pytest.raises(WorkflowError):
            svc.resolve_conversation(
                payload(workspace, shot_id, stage="storyboard", subject_type="shot")
            )
        with session.begin():
            current = svc._conversation(original.id)
            with pytest.raises(WorkflowError):
                svc.check_expected_scope(current, scope(first), validate_subject=True)


def test_parallel_first_resolve_returns_one_current_conversation(workspace):
    factory, project_id, episode_id, _ = workspace
    values = payload(
        workspace, episode_id, stage="source", subject_type="episode", task_type="writing"
    )
    barrier = Barrier(2)

    def resolve(_):
        with factory() as session:
            svc = service(session)
            barrier.wait(timeout=5)
            return svc.resolve_conversation(values).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(resolve, (1, 2)))
    assert first == second
    with factory() as session:
        page = service(session).list_conversations(project_id, episode_id, scope=scope(values))
        assert page.total == 1
    with factory() as session:
        assert session.scalar(select(func.count(AgentConversation.id))) == 1


def test_scope_identity_cannot_be_changed_after_creation(workspace):
    factory, _, _, _ = workspace
    identifiers, _, _ = subjects(workspace)
    with factory() as session:
        created = service(session).resolve_conversation(payload(workspace, identifiers[0]))
    with factory() as session, session.begin():
        service(session)
        current = session.get(AgentConversation, created.id)
        current.subject_id = identifiers[1]
        with pytest.raises(WorkflowError) as changed:
            session.flush()
        assert changed.value.code == "ownership_immutable"
        session.rollback()


def test_candidates_list_total_detail_and_adoption_follow_their_object_conversation(workspace):
    factory, project_id, episode_id, _ = workspace
    identifiers, shot_id, _ = subjects(workspace)
    scopes = [payload(workspace, identifier) for identifier in identifiers[:2]]
    scopes.append(payload(workspace, shot_id, stage="storyboard", subject_type="shot"))
    candidates = []
    for values in scopes:
        kind = "asset_patch" if values["subject_type"] == "asset" else "shot_patch"
        patch = {"description": "修订人物动机"} if kind == "asset_patch" else {"script": "修订动作"}
        run_id, tool_id, args = artifact_tests.prepared(
            factory,
            project_id,
            episode_id,
            kind=kind,
            target_id=values["subject_id"],
            content="",
            patch=patch,
        )
        # Reuse the existing durable candidate seed, assigning stable scope in the
        # trusted fixture session before effects. Production scope is immutable.
        with factory.begin() as session:
            run = session.get(AgentRun, run_id)
            conversation = session.get(AgentConversation, run.conversation_id)
            for name in ("stage", "subject_type", "subject_id", "task_type"):
                setattr(conversation, name, values[name])
            conversation.scope_version = 1
        candidates.append(artifact_tests.create(factory, run_id, tool_id, args)["artifact_id"])
    for index, values in enumerate(scopes):
        with factory() as session:
            svc = artifact_tests.artifact_service(session)
            page = svc.list(project_id, episode_id, expected_scope=scope(values))
            assert page["total"] == 1 and page["items"][0]["id"] == candidates[index]
            own = svc.get(project_id, episode_id, candidates[index], expected_scope=scope(values))
            other = candidates[(index + 1) % len(candidates)]
            with pytest.raises(NotFound):
                svc.get(project_id, episode_id, other, expected_scope=scope(values))
            with pytest.raises(NotFound):
                svc.adopt(
                    project_id,
                    episode_id,
                    other,
                    artifact_tests.adopt_body(own),
                    expected_scope=scope(values),
                )
    with factory() as session:
        detail = artifact_tests.artifact_service(session).get(
            project_id, episode_id, candidates[0], expected_scope=scope(scopes[0])
        )
        adopted = artifact_tests.artifact_service(session).adopt(
            project_id,
            episode_id,
            candidates[0],
            {**artifact_tests.adopt_body(detail), "confirm_shared": True},
            expected_scope=scope(scopes[0]),
        )
        assert adopted["status"] == "applied"


def authenticated_client(factory):
    cfg = run_tests.settings()
    app = create_app(cfg)
    app.state.session_factory, app.state.agent_schema_ready = factory, True
    token, csrf = "scoped-token", "scoped-csrf"
    with factory.begin() as session:
        session.add(
            UserSession(
                id=next_id(),
                user_id=1,
                token_hash=token_hash(token),
                csrf_hash=token_hash(csrf),
                expires_at=conversation_tests.utcnow() + timedelta(days=1),
                created_at=conversation_tests.utcnow(),
            )
        )
    client = TestClient(app)
    client.cookies.set("sd_session", token)
    client.headers.update({"Origin": cfg.public_origin, "X-CSRF-Token": csrf})
    return client


def test_workspace_api_checks_scope_before_messages_state_attachments_runs_and_events(workspace):
    factory, project_id, episode_id, _ = workspace
    identifiers, _, _ = subjects(workspace)
    first, second = (payload(workspace, identifier) for identifier in identifiers[:2])
    with factory() as session:
        conversation = service(session).resolve_conversation(first)
    client = authenticated_client(factory)
    base = f"/api/v1/agent/conversations/{conversation.id}"
    matching = scope(first).model_dump(mode="json", exclude_none=True)
    mismatching = scope(second).model_dump(mode="json", exclude_none=True)
    assert client.get(base, params=matching).status_code == 200
    assert client.get(base, params=mismatching).status_code == 409
    for suffix in ("messages", "state", "attachments", "runs", "events"):
        wrong = client.get(base + "/" + suffix, params=mismatching)
        assert wrong.status_code == 409, (suffix, wrong.text)
        assert wrong.json()["error"]["code"] == "agent_scope_mismatch"
    assert client.get(base + "/messages", params=matching).json()["total"] == 0
    assert client.get(base + "/state", params=matching).status_code == 200
    assert (
        client.post(
            base + "/messages",
            json={"content": "只讨论当前人物", "expected_scope": mismatching},
            headers={"Idempotency-Key": "wrong-scoped-message"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            base + "/attachments/references",
            params=mismatching,
            json={"source_type": "asset", "source_id": str(identifiers[0])},
            headers={"Idempotency-Key": "wrong-scoped-reference"},
        ).status_code
        == 409
    )
    assert (
        client.get(
            "/api/v1/agent/conversations",
            params={"project_id": str(project_id), "episode_id": str(episode_id), **matching},
        ).json()["total"]
        == 1
    )


def test_legacy_http_records_are_readonly_and_new_http_creation_requires_scope(workspace):
    factory, project_id, episode_id, _ = workspace
    conversation_id, model_id = run_tests.setup(factory, project_id, episode_id)
    accepted = run_tests.send(factory, conversation_id, model_id)
    client = authenticated_client(factory)
    base = f"/api/v1/agent/conversations/{conversation_id}"
    run_base = f"/api/v1/agent/runs/{accepted.run.id}"
    assert client.get(base).status_code == 200
    assert client.get(base + "/messages").json()["total"] == 1
    body = {"project_id": str(project_id), "episode_id": str(episode_id)}
    assert client.post("/api/v1/agent/conversations", json=body).status_code == 422
    assert client.post("/api/v1/agent/conversations/resolve", json=body).status_code == 422
    mutations = [
        client.patch(base, json={"row_version": 1, "title": "试图改名"}),
        client.post(
            base + "/messages",
            json={"content": "新增要求"},
            headers={"Idempotency-Key": "legacy-write"},
        ),
        client.post(
            base + "/attachments/uploads",
            files={"file": ("note.md", b"note")},
            headers={"Idempotency-Key": "legacy-upload"},
        ),
        client.post(
            base + "/attachments/references",
            json={"source_type": "asset", "source_id": str(next_id())},
            headers={"Idempotency-Key": "legacy-reference"},
        ),
        client.delete(base + f"/attachments/{next_id()}"),
        client.post(
            run_base + f"/reviews/{next_id()}",
            json={"review_version": 1, "review_hash": "a" * 64, "decision": "approved"},
        ),
        client.post(
            run_base + "/continue", json={"artifact_id": str(next_id()), "artifact_row_version": 1}
        ),
    ]
    for response in mutations:
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "agent_legacy_conversation_readonly"
    assert client.post(run_base + "/stop").json()["status"] == "cancelled"
