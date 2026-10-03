"""Shared candidate/adoption boundaries on real disposable MySQL, no provider calls."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier

import pytest
import test_agent_conversations as conversation_tests
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from test_agent_conversations import actor
from test_agent_services import send, settings, setup

from short_drama.agent.artifacts import create_candidate_locked
from short_drama.agent.authorization import digest, freeze_task
from short_drama.agent.runtime import lock_run
from short_drama.agent.tools import CreateCandidate, execute_tools
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import token_hash
from short_drama.domain import (
    AgentArtifact,
    AgentToolCall,
    AgentTurn,
    Asset,
    Episode,
    EpisodeAsset,
    EpisodeNovel,
    EpisodeScript,
    MediaFile,
    ProjectAsset,
    ProjectMember,
    ShotScript,
    UserSession,
)
from short_drama.main import create_app
from short_drama.service.agent_artifact_service import AgentArtifactService
from short_drama.service.base import utcnow
from short_drama.service.episode_writing_service import EpisodeWritingService
from short_drama.service.shot_script_service import ShotScriptService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


def writing(factory, project_id, episode_id, content="Original"):
    with factory() as session:
        session.info["actor"] = actor(1)
        return EpisodeWritingService(session).save_novel(
            project_id, episode_id, {"content_version": 1, "content": content}
        )


def prepared(
    factory,
    project_id,
    episode_id,
    *,
    kind="novel",
    target_id=None,
    content="Shared proposal",
    patch=None,
    mode="single",
    secret=None,
):
    conversation_id, model_id = setup(factory, project_id, episode_id)
    accepted = send(
        factory,
        conversation_id,
        model_id,
        mode="generate",
        task={"kind": kind, "target_id": target_id, "instructions": "PRIVATE_INSTRUCTIONS"},
    )
    run_id = int(accepted.run.id)
    with factory() as session, session.begin():
        session.info["actor"] = actor(1)
        _, conversation, run = lock_run(session, run_id)
        checkpoint = deepcopy(run.checkpoint)
        checkpoint["authorization"]["mode"] = mode
        if mode == "workflow":
            checkpoint["authorization"]["approved_plan"] = {"hash": "approved"}
        run.checkpoint = checkpoint
        if secret:
            config = deepcopy(run.config_snapshot)
            config["credential_cipher"] = KeyCipher(base64.b64encode(b"0" * 32).decode()).encrypt(
                secret
            )
            run.config_snapshot = config
        args = {
            "step_id": checkpoint["authorization"]["steps"][0]["id"],
            "content": content,
            "patch": patch or {},
        }
        turn = AgentTurn(
            id=next_id(),
            run_id=run.id,
            turn_no=1,
            status="succeeded",
            response={
                "raw": {"private": "PRIVATE_PROVIDER"},
                "normalized": {"output_kind": "tool_requests"},
                "applied": True,
            },
        )
        session.add(turn)
        session.flush()
        tool = AgentToolCall(
            id=next_id(),
            run_id=run.id,
            turn_id=turn.id,
            call_index=1,
            provider_call_id="PRIVATE_CALL",
            tool_name="create_candidate",
            arguments={"raw": json.dumps(args), "parsed": args},
            arguments_hash=digest(args),
            idempotency_key=digest(["candidate", run.id]),
        )
        session.add(tool)
        run.status, run.phase, run.message_status = "running", "tools", "idle"
        session.flush()
        return run_id, tool.id, args


def create(factory, run_id, tool_id, args):
    with factory() as session, session.begin():
        session.info["actor"] = actor(1)
        _, conversation, run = lock_run(session, run_id)
        tool = session.get(AgentToolCall, tool_id)
        return create_candidate_locked(
            session,
            conversation,
            run,
            tool,
            CreateCandidate.model_validate(args),
            settings=settings(),
        )


def artifact_service(session, owner=1):
    session.info["actor"] = actor(owner)
    return AgentArtifactService(session)


def details(factory, p, e, artifact_id, owner=1):
    with factory() as session:
        return artifact_service(session, owner).get(p, e, artifact_id)


def adopt_body(detail):
    source = detail["source_snapshot"]
    return {
        "row_version": detail["row_version"],
        "content_version": source["content_version"],
        "storyboard_version": source["storyboard_version"],
        "target_row_version": source["target_row_version"],
    }


def test_script_creation_changes_no_editor_pointer_confirmation_or_version(workspace):
    factory, p, e, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        original = EpisodeWritingService(session).save_script(
            p, e, {"content_version": 1, "script_id": None, "content": "Original script"}
        )
        EpisodeWritingService(session).confirm(
            p, e, original["script"]["id"], {"content_version": 2}
        )
    run_id, tool_id, args = prepared(factory, p, e, kind="script", content="New script")
    result = create(factory, run_id, tool_id, args)
    assert create(factory, run_id, tool_id, args) == result
    detail = details(factory, p, e, result["artifact_id"])
    with factory.begin() as session:
        episode = session.get(Episode, e)
        assert episode.content_version == 3 and episode.storyboard_version == 1
        assert episode.editing_script_id == int(original["script"]["id"])
        assert session.get(EpisodeScript, episode.editing_script_id).state == "confirmed"
        assert session.get(EpisodeScript, int(detail["script_id"])).state == "unconfirmed"
        assert session.scalar(select(func.count(AgentArtifact.id))) == 1
    with factory() as session:
        adopted = artifact_service(session, 2).adopt(
            p, e, result["artifact_id"], adopt_body(detail)
        )
    assert adopted["apply_receipt"]["action"] == "select_script"
    with factory.begin() as session:
        episode = session.get(Episode, e)
        assert episode.content_version == 4 and episode.editing_script_id == int(
            detail["script_id"]
        )
        assert session.get(EpisodeScript, int(detail["script_id"])).state == "unconfirmed"
        assert session.get(EpisodeScript, int(original["script"]["id"])).state == "confirmed"


def test_shared_novel_read_adopt_and_replay_never_read_private_origin(workspace):
    factory, p, e, _ = workspace
    writing(factory, p, e)
    run_id, tool_id, args = prepared(factory, p, e, content="New novel")
    result = create(factory, run_id, tool_id, args)
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement.lower())

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with factory() as session:
            svc = artifact_service(session, 2)
            listing = svc.list(p, e)
            detail = svc.get(p, e, result["artifact_id"])
            adopted = svc.adopt(p, e, result["artifact_id"], adopt_body(detail))
        with factory() as session:
            session.info["actor"] = actor(2)
            EpisodeWritingService(session).save_novel(
                p, e, {"content_version": 3, "content": "Later edit"}
            )
        with factory() as session:
            replay = artifact_service(session, 2).adopt(
                p, e, result["artifact_id"], adopt_body(detail)
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert replay["apply_receipt"] == adopted["apply_receipt"]
    assert replay["row_version"] == adopted["row_version"] == 2
    assert listing["items"][0]["preview"] == "New novel"
    public = json.dumps([listing, detail, adopted])
    for forbidden in (
        "PRIVATE",
        "conversation_id",
        "run_id",
        "tool_call_id",
        "credential",
        "instructions",
    ):
        assert forbidden not in public
    for table in (
        "agent_conversations",
        "agent_messages",
        "agent_runs",
        "agent_turns",
        "agent_tool_calls",
    ):
        assert not any(table in stmt for stmt in statements)
    with factory.begin() as session:
        assert (
            session.scalar(select(EpisodeNovel.content).where(EpisodeNovel.episode_id == e))
            == "Later edit"
        )


def test_stale_source_and_stale_artifact_reject_without_partial_adoption(workspace):
    factory, p, e, _ = workspace
    writing(factory, p, e)
    run_id, tool_id, args = prepared(factory, p, e)
    result = create(factory, run_id, tool_id, args)
    detail = details(factory, p, e, result["artifact_id"])
    with factory() as session:
        with pytest.raises(WorkflowError) as conflict:
            artifact_service(session).adopt(
                p, e, result["artifact_id"], {**adopt_body(detail), "row_version": 2}
            )
        assert conflict.value.code == "agent_artifact_version_conflict"
        EpisodeWritingService(session).save_novel(
            p, e, {"content_version": 2, "content": "Manual edit"}
        )
        with pytest.raises(WorkflowError) as stale:
            artifact_service(session).adopt(
                p, e, result["artifact_id"], {**adopt_body(detail), "content_version": 3}
            )
        assert stale.value.code == "agent_source_changed"
    assert details(factory, p, e, result["artifact_id"])["status"] == "ready"
    with factory.begin() as session:
        assert (
            session.scalar(select(EpisodeNovel.content).where(EpisodeNovel.episode_id == e))
            == "Manual edit"
        )


def test_workflow_candidate_waits_for_explicit_adopt_and_continue(workspace):
    factory, p, e, _ = workspace
    run_id, tool_id, args = prepared(factory, p, e, mode="workflow")
    execute_tools(factory, settings(), run_id)
    with factory.begin() as session:
        _, _, run = lock_run(session, run_id)
        assert run.status == "waiting_review" and run.phase == "wait"
        assert run.checkpoint["awaiting_step_id"] == args["step_id"]
        artifact_id = run.checkpoint["awaiting_artifacts"][0]
        assert session.get(AgentToolCall, tool_id).status == "succeeded"
    detail = details(factory, p, e, artifact_id)
    with factory() as session:
        artifact_service(session, 2).adopt(p, e, artifact_id, adopt_body(detail))
    with factory.begin() as session:
        assert lock_run(session, run_id)[2].status == "waiting_review"


def test_concurrent_duplicate_adoption_has_one_version_change_and_one_receipt(workspace):
    factory, p, e, _ = workspace
    writing(factory, p, e)
    run_id, tool_id, args = prepared(factory, p, e, content="Concurrent candidate")
    result = create(factory, run_id, tool_id, args)
    detail = details(factory, p, e, result["artifact_id"])
    barrier = Barrier(2)

    def apply(owner):
        with factory() as session:
            barrier.wait(timeout=5)
            return artifact_service(session, owner).adopt(
                p, e, result["artifact_id"], adopt_body(detail)
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(apply, [1, 2]))
    assert first["apply_receipt"] == second["apply_receipt"]
    assert first["applied_by"] == second["applied_by"]
    with factory.begin() as session:
        assert session.get(Episode, e).content_version == 3
        assert session.get(AgentArtifact, int(result["artifact_id"])).row_version == 2


def test_failed_receipt_transaction_rolls_back_business_change(workspace):
    factory, p, e, _ = workspace
    writing(factory, p, e)
    run_id, tool_id, args = prepared(factory, p, e, content="Candidate")
    result = create(factory, run_id, tool_id, args)
    detail = details(factory, p, e, result["artifact_id"])
    with factory() as session:

        def fail_receipt(current, *_):
            if any(
                isinstance(obj, AgentArtifact) and obj.status == "applied" for obj in current.dirty
            ):
                raise RuntimeError("Simulated receipt persistence failure")

        event.listen(session, "before_flush", fail_receipt)
        with pytest.raises(RuntimeError, match="receipt"):
            artifact_service(session).adopt(p, e, result["artifact_id"], adopt_body(detail))
    with factory.begin() as session:
        assert session.get(Episode, e).content_version == 2
        assert (
            session.scalar(select(EpisodeNovel.content).where(EpisodeNovel.episode_id == e))
            == "Original"
        )
        assert session.get(AgentArtifact, int(result["artifact_id"])).status == "ready"


def test_source_changed_after_authorization_creates_no_candidate(workspace):
    factory, p, e, _ = workspace
    run_id, tool_id, args = prepared(factory, p, e)
    writing(factory, p, e, content="Edit after scope was frozen")
    execute_tools(factory, settings(), run_id)
    with factory.begin() as session:
        assert session.scalar(select(func.count(AgentArtifact.id))) == 0
        assert session.get(AgentToolCall, tool_id).error["code"] == "agent_source_changed"


def test_approved_workflow_cannot_skip_an_unconsumed_step(workspace):
    factory, p, e, _ = workspace
    run_id, tool_id, args = prepared(factory, p, e, mode="workflow")
    with factory() as session, session.begin():
        session.info["actor"] = actor(1)
        _, conversation, run = lock_run(session, run_id)
        checkpoint = deepcopy(run.checkpoint)
        second = freeze_task(
            session,
            conversation,
            {"kind": "script", "instructions": "Second"},
            step_id="step-2",
            owner_user_id=1,
        )
        checkpoint["authorization"]["steps"].append(second)
        run.checkpoint = checkpoint
    with pytest.raises(WorkflowError, match="order"):
        create(factory, run_id, tool_id, {**args, "step_id": "step-2"})
    with factory.begin() as session:
        assert session.scalar(select(func.count(AgentArtifact.id))) == 0
        assert lock_run(session, run_id)[2].checkpoint["authorization"]["consumed_steps"] == []


def test_patch_rejects_changed_target_even_when_episode_source_is_same(workspace):
    factory, p, e, _ = workspace
    asset_id = linked_asset(factory, p, e)
    run_id, tool_id, args = prepared(
        factory,
        p,
        e,
        kind="asset_patch",
        target_id=str(asset_id),
        content="",
        patch={"name": "Candidate name"},
    )
    result = create(factory, run_id, tool_id, args)
    detail = details(factory, p, e, result["artifact_id"])
    with factory.begin() as session:
        asset = session.get(Asset, asset_id)
        asset.name, asset.row_version = "Changed after generation", 2
    with factory() as session:
        with pytest.raises(WorkflowError) as changed:
            artifact_service(session).adopt(
                p, e, result["artifact_id"], {**adopt_body(detail), "confirm_shared": True}
            )
        assert changed.value.code == "asset_version_conflict"
    assert details(factory, p, e, result["artifact_id"])["status"] == "ready"


def linked_asset(factory, p, e, *, linked=True):
    with factory() as session, session.begin():
        session.info["actor"] = actor(1)
        media = MediaFile(
            id=next_id(),
            project_id=p,
            format_code="image/png",
            storage_locator="mock:asset:" + str(next_id()),
        )
        session.add(media)
        session.flush()
        asset = Asset(
            id=next_id(),
            project_id=p,
            kind="character",
            name="Hero",
            state="confirmed",
            media_id=media.id,
        )
        session.add(asset)
        session.flush()
        session.add(ProjectAsset(id=next_id(), project_id=p, asset_id=asset.id, position=1))
        if linked:
            session.add(EpisodeAsset(id=next_id(), episode_id=e, asset_id=asset.id, position=1))
        return asset.id


def test_asset_patch_shared_confirmation_diff_and_atomic_receipt(workspace):
    factory, p, e, _ = workspace
    asset_id = linked_asset(factory, p, e)
    run_id, tool_id, args = prepared(
        factory,
        p,
        e,
        kind="asset_patch",
        target_id=str(asset_id),
        content="",
        patch={"name": "New hero", "tags": [" brave ", "brave"]},
    )
    result = create(factory, run_id, tool_id, args)
    detail = details(factory, p, e, result["artifact_id"])
    assert detail["patch"] == {"name": "New hero", "tags": ["brave"]}
    assert detail["diff"][0] == {"field": "name", "before": "Hero", "after": "New hero"}
    with factory() as session:
        with pytest.raises(WorkflowError) as shared:
            artifact_service(session, 2).adopt(p, e, result["artifact_id"], adopt_body(detail))
        assert shared.value.code == "shared_asset_confirmation_required"
        assert shared.value.details["reference_count"] >= 2
        adopted = artifact_service(session, 2).adopt(
            p, e, result["artifact_id"], {**adopt_body(detail), "confirm_shared": True}
        )
    assert adopted["apply_receipt"]["target_row_version"] == 2
    with factory.begin() as session:
        asset = session.get(Asset, asset_id)
        assert asset.name == "New hero" and asset.tags == ["brave"] and asset.state == "unconfirmed"
        assert session.get(Episode, e).content_version == 1


def test_project_only_asset_is_rejected_when_freezing_scope(workspace):
    factory, p, e, _ = workspace
    asset_id = linked_asset(factory, p, e, linked=False)
    conversation_id, model_id = setup(factory, p, e)
    with pytest.raises(NotFound, match="episode"):
        send(
            factory,
            conversation_id,
            model_id,
            mode="generate",
            task={"kind": "asset_patch", "target_id": str(asset_id), "instructions": "Edit hero"},
        )


def test_shot_patch_preserves_excerpt_and_bumps_shot_and_storyboard(workspace):
    factory, p, e, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        shot = ShotScriptService(session).create(
            {"episode_id": e, "position": 1, "script": "Before"}
        )
    with factory.begin() as session:
        session.get(ShotScript, shot.id).source_excerpt = "Original immutable excerpt"
    run_id, tool_id, args = prepared(
        factory,
        p,
        e,
        kind="shot_patch",
        target_id=str(shot.id),
        content="",
        patch={"script": "After", "duration_ms": 5000, "video_prompt": "Camera follows hero"},
    )
    result = create(factory, run_id, tool_id, args)
    detail = details(factory, p, e, result["artifact_id"])
    with factory() as session:
        adopted = artifact_service(session, 2).adopt(
            p, e, result["artifact_id"], adopt_body(detail)
        )
    assert adopted["apply_receipt"]["target_row_version"] == 2
    assert adopted["apply_receipt"]["storyboard_version"] == 3
    with factory.begin() as session:
        target = session.get(ShotScript, shot.id)
        assert target.source_excerpt == "Original immutable excerpt"
        assert target.script == "After" and target.duration_ms == 5000
        assert target.video_prompt == "Camera follows hero"


def test_shot_patch_rejects_changed_storyboard_with_same_target_version(workspace):
    factory, p, e, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        shot = ShotScriptService(session).create(
            {"episode_id": e, "position": 1, "script": "Before"}
        )
    run_id, tool_id, args = prepared(
        factory,
        p,
        e,
        kind="shot_patch",
        target_id=str(shot.id),
        content="",
        patch={"script": "After"},
    )
    result = create(factory, run_id, tool_id, args)
    detail = details(factory, p, e, result["artifact_id"])
    with factory.begin() as session:
        session.get(Episode, e).storyboard_version += 1
    with factory() as session:
        with pytest.raises(WorkflowError) as stale:
            artifact_service(session).adopt(p, e, result["artifact_id"], adopt_body(detail))
        assert stale.value.code == "agent_source_changed"
    with factory.begin() as session:
        assert session.get(ShotScript, shot.id).script == "Before"
        assert session.get(AgentArtifact, int(result["artifact_id"])).status == "ready"


def test_candidate_public_content_redacts_secret_but_private_intent_is_exact(workspace):
    factory, p, e, _ = workspace
    run_id, tool_id, args = prepared(
        factory, p, e, content="Candidate says TEST_SECRET then ends", secret="TEST_SECRET"
    )
    result = create(factory, run_id, tool_id, args)
    assert (
        details(factory, p, e, result["artifact_id"])["content"]
        == "Candidate says [redacted] then ends"
    )
    with factory.begin() as session:
        assert session.get(AgentToolCall, tool_id).arguments["parsed"] == args
        assert "TEST_SECRET" in session.get(AgentToolCall, tool_id).arguments["raw"]


@pytest.mark.parametrize(
    "patch",
    [{"source_excerpt": "replace"}, {"position": 2}, {"script": None}, {"duration_ms": 500}],
)
def test_invalid_shot_patch_has_no_shared_effect(workspace, patch):
    factory, p, e, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        shot = ShotScriptService(session).create({"episode_id": e, "position": 1})
    run_id, tool_id, _ = prepared(
        factory, p, e, kind="shot_patch", target_id=str(shot.id), content="", patch=patch
    )
    execute_tools(factory, settings(), run_id)
    with factory.begin() as session:
        assert session.scalar(select(func.count(AgentArtifact.id))) == 0
        assert session.get(AgentToolCall, tool_id).error["code"] == "invalid_tool_arguments"
        assert session.get(ShotScript, shot.id).row_version == 1


def test_feature_disabled_shared_api_still_reads_adopts_and_enforces_membership(workspace):
    factory, p, e, member_id = workspace
    run_id, tool_id, args = prepared(factory, p, e)
    result = create(factory, run_id, tool_id, args)
    cfg = settings().model_copy(update={"agent_enabled": False})
    app = create_app(cfg)
    # Root connects this router for production. Include here only if that edit has
    # not landed yet, so this test also exercises the independent module contract.
    from short_drama.api.v1.agent_artifacts import router

    if not any(getattr(route, "path", "").endswith("agent-artifacts") for route in app.routes):
        app.include_router(router, prefix="/api/v1")
    app.state.session_factory, app.state.agent_schema_ready = factory, True
    token, csrf = "artifact-session", "artifact-csrf"
    with factory.begin() as session:
        session.add(
            UserSession(
                id=next_id(),
                user_id=2,
                token_hash=token_hash(token),
                csrf_hash=token_hash(csrf),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    client = TestClient(app)
    client.cookies.set("sd_session", token)
    client.headers.update({"Origin": cfg.public_origin, "X-CSRF-Token": csrf})
    path = f"/api/v1/projects/{p}/episodes/{e}/agent-artifacts"
    listed = client.get(path)
    assert listed.status_code == 200, listed.text
    detail = client.get(path + "/" + result["artifact_id"])
    assert detail.status_code == 200, detail.text
    assert type(detail.json()["row_version"]) is int
    assert type(detail.json()["source_snapshot"]["content_version"]) is int
    adopted = client.post(
        path + "/" + result["artifact_id"] + "/adopt", json=adopt_body(detail.json())
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["applied_by"] == "2"
    with factory.begin() as session:
        session.get(ProjectMember, member_id).status = "removed"
    assert client.get(path).status_code == 404
    assert (
        client.post(
            path + "/" + result["artifact_id"] + "/adopt", json=adopt_body(detail.json())
        ).status_code
        == 404
    )
