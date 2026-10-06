"""Native parameter freeze and context privacy on disposable MySQL, no providers."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
import test_agent_conversations as conversation_tests
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_agent_conversations import actor, service
from test_agent_services import add_model, send, settings, setup

from short_drama.agent.authorization import freeze_task
from short_drama.agent.tools import _read_context
from short_drama.core.exceptions import WorkflowError
from short_drama.core.identity import token_hash
from short_drama.domain import (
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentTurn,
    AIGenerationRecord,
    AIModelConfig,
    Asset,
    AsyncTask,
    Episode,
    EpisodeAsset,
    EpisodeScript,
    MediaFile,
    ProjectAsset,
    ShotAsset,
    ShotImage,
    ShotScript,
    UserSession,
)
from short_drama.main import create_app
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.generation_context_service import GenerationContextService
from short_drama.service.shot_script_service import ShotScriptService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


def media_model(factory, kind, *, owner=1, enabled=1, deleted=0, name=None):
    with factory.begin() as session:
        row = AIModelConfig(
            id=next_id(),
            owner_user_id=owner,
            service_type=kind,
            name=name or f"Private {kind}",
            provider="ark" if kind == "image" else "modelhub",
            model_key="fixture" if kind == "image" else "seedance-2.0-mini",
            base_url="https://private-native-endpoint.invalid/v1",
            apikey="PRIVATE_KEY_ENVELOPE",
            enabled=enabled,
            is_deleted=deleted,
        )
        session.add(row)
        return row.id


def asset(factory, project_id, episode_id, *, name="Hero", position=1):
    with factory.begin() as session:
        row = Asset(
            id=next_id(),
            project_id=project_id,
            kind="character",
            name=name,
            prompt="Hero in a blue coat",
        )
        session.add(row)
        session.flush()
        session.add(
            ProjectAsset(id=next_id(), project_id=project_id, asset_id=row.id, position=position)
        )
        session.add(EpisodeAsset(id=next_id(), episode_id=episode_id, asset_id=row.id, position=1))
        return row.id


def shot(factory, project_id, episode_id, *, reference=False):
    with factory() as session:
        session.info["actor"] = actor(1)
        row = ShotScriptService(session).create(
            {"episode_id": episode_id, "position": 1, "script": "The hero enters the room"}
        )
    with factory.begin() as session:
        saved = session.get(ShotScript, row.id)
        saved.image_settings = {"resolution": "4K", "aspect": "inherit", "layout": "nine"}
        saved.video_settings = {"resolution": "720p", "duration_ms": 3000}
        saved.video_prompt = "Saved editor camera prompt"
        session.flush()
        if reference:
            *_, context_hash = GenerationContextService(session).locked_shot_context(row.id)
            image = MediaFile(
                id=next_id(),
                project_id=project_id,
                format_code="image/png",
                storage_locator=f"minio://images/parameter-frame-{next_id()}.png",
            )
            session.add(image)
            session.flush()
            session.add(
                ShotImage(
                    id=next_id(),
                    episode_id=episode_id,
                    shot_id=row.id,
                    media_id=image.id,
                    context_hash=context_hash,
                    aspect="16:9",
                )
            )
        return row.id


def native_scope(workspace, kind):
    factory, project_id, episode_id, _ = workspace
    decision_id = add_model(factory)
    payload = {"kind": kind, "instructions": "Generate this approved candidate"}
    if kind in {"extract", "storyboard"}:
        with factory.begin() as session:
            script = EpisodeScript(
                id=next_id(),
                episode_id=episode_id,
                position=1,
                state="confirmed",
                content="The hero enters the room.",
            )
            session.add(script)
            session.flush()
            session.get(Episode, episode_id).editing_script_id = script.id
    else:
        model_id = media_model(factory, kind)
        target_id = (
            asset(factory, project_id, episode_id)
            if kind == "image"
            else shot(factory, project_id, episode_id, reference=True)
        )
        payload.update(model_config_id=str(model_id), target_id=str(target_id))
        if kind == "image":
            payload["parameters"] = {"target_kind": "asset"}
    subject_type = (
        "episode" if kind in {"extract", "storyboard"} else ("asset" if kind == "image" else "shot")
    )
    with factory() as session:
        conversation = service(session).create_conversation(
            {
                "project_id": project_id,
                "episode_id": episode_id,
                "stage": "assets" if kind in {"extract", "image"} else "storyboard",
                "subject_type": subject_type,
                "subject_id": episode_id if subject_type == "episode" else payload["target_id"],
                "task_type": {
                    "extract": "extraction",
                    "storyboard": "planning",
                    "image": "image",
                    "video": "video",
                }[kind],
            }
        )
    return int(conversation.id), decision_id, payload


def message_client(factory):
    cfg = settings()
    app = create_app(cfg)
    app.state.session_factory, app.state.agent_schema_ready = factory, True
    token, csrf = "parameter-session", "parameter-csrf"
    with factory.begin() as session:
        session.add(
            UserSession(
                id=next_id(),
                user_id=1,
                token_hash=token_hash(token),
                csrf_hash=token_hash(csrf),
                expires_at=utcnow() + timedelta(days=1),
                created_at=utcnow(),
            )
        )
    client = TestClient(app)
    client.cookies.set("sd_session", token)
    client.headers.update({"Origin": cfg.public_origin, "X-CSRF-Token": csrf})
    return client


@pytest.mark.parametrize("aspect", ["16:9", "9:16"])
def test_asset_image_defaults_are_explicitly_frozen_before_admission(workspace, aspect):
    factory, _, episode_id, _ = workspace
    conversation_id, decision_id, payload = native_scope(workspace, "image")
    with factory.begin() as session:
        session.get(Episode, episode_id).aspect = aspect
    accepted = send(factory, conversation_id, decision_id, mode="generate", task=payload)
    with factory.begin() as session:
        run = session.get(AgentRun, int(accepted.run.id))
        step = run.checkpoint["authorization"]["steps"][0]
        assert step["parameters"] == {
            "target_kind": "asset",
            "aspect": aspect,
            "resolution": "2K",
            "reference_media_ids": [],
        }
        assert step["target_label"] == "素材：Hero"
        assert step["target_id"] not in step["target_label"]
        assert run.budget["images"] == 1 and run.usage["images"] == 0
        assert session.scalar(select(func.count(AsyncTask.id))) == 0
        assert session.scalar(select(func.count(AIGenerationRecord.id))) == 0


INVALID = [
    ("image", {"aspect": "PRIVATE_BAD_ASPECT"}),
    ("video", {"aspect": "PRIVATE_BAD_ASPECT"}),
    ("video", {"duration_ms": 0}),
    ("video", {"duration_ms": "PRIVATE_BAD_DURATION"}),
    ("extract", {"unknown_option": "PRIVATE_BAD_OPTION"}),
    ("storyboard", {"unknown_option": "PRIVATE_BAD_OPTION"}),
    ("storyboard", {"average_shot_duration_ms": 999}),
]


@pytest.mark.parametrize("entry", ["send_api", "plan_freeze"])
@pytest.mark.parametrize("kind,invalid", INVALID)
def test_invalid_native_parameters_fail_safely_before_run_or_quota_or_task(
    workspace, entry, kind, invalid
):
    factory, _, _, _ = workspace
    conversation_id, decision_id, payload = native_scope(workspace, kind)
    payload["parameters"] = {**payload.get("parameters", {}), **invalid}
    if entry == "send_api":
        response = message_client(factory).post(
            f"/api/v1/agent/conversations/{conversation_id}/messages",
            json={
                "content": "Generate the candidate",
                "mode": "generate",
                "model_config_id": str(decision_id),
                "task": payload,
            },
            headers={"Idempotency-Key": "invalid-native-parameters"},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_agent_parameters"
        assert "PRIVATE" not in response.text and "unknown_option" not in response.text
    else:
        with factory() as session, session.begin():
            session.info["actor"] = actor(1)
            conversation = session.get(AgentConversation, conversation_id)
            with pytest.raises(WorkflowError) as rejected:
                freeze_task(session, conversation, payload, step_id="step-1", owner_user_id=1)
            assert (
                rejected.value.status_code == 422
                and rejected.value.code == "invalid_agent_parameters"
            )
            assert "PRIVATE" not in rejected.value.message and rejected.value.__cause__ is None
    with factory.begin() as session:
        for model in (AgentRun, AgentMessage, AgentTurn, AsyncTask, AIGenerationRecord):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        conversation = session.get(AgentConversation, conversation_id)
        assert conversation.next_message_seq == 1


@pytest.mark.parametrize("kind", ["image", "video"])
def test_shot_media_overrides_are_frozen_without_mutating_editor(workspace, kind):
    factory, project_id, episode_id, _ = workspace
    conversation_id, decision_id = setup(factory, project_id, episode_id)
    model_id = media_model(factory, kind)
    shot_id = shot(factory, project_id, episode_id, reference=kind == "video")
    parameters = {"target_kind": "shot", "aspect": "9:16", "resolution": "1K", "layout": "four"}
    if kind == "video":
        parameters = {
            "target_kind": "shot",
            "aspect": "9:16",
            "resolution": "480p",
            "duration_ms": 5000,
        }
    with factory.begin() as session:
        saved = session.get(ShotScript, shot_id)
        original = {
            key: deepcopy(getattr(saved, key))
            for key in (
                "image_settings",
                "video_settings",
                "video_prompt",
                "row_version",
                "script",
                "duration_ms",
            )
        }
        storyboard_version = session.get(Episode, episode_id).storyboard_version
    accepted = send(
        factory,
        conversation_id,
        decision_id,
        mode="generate",
        task={
            "kind": kind,
            "target_id": str(shot_id),
            "model_config_id": str(model_id),
            "instructions": "Approved candidate override",
            "parameters": parameters,
        },
    )
    with factory.begin() as session:
        run = session.get(AgentRun, int(accepted.run.id))
        step = run.checkpoint["authorization"]["steps"][0]
        expected_references = []
        if kind == "video":
            expected_references = [step["source"]["reference_media_id"]]
        assert step["parameters"] == {
            **parameters,
            "reference_media_ids": expected_references,
        }
        assert step["target_label"] == "第 1 个镜头：The hero enters the room"
        assert str(shot_id) not in step["target_label"]
        assert step["source"]["target_row_version"] == original["row_version"]
        assert len(step["source"]["shot_context_hash"]) == 64
        if kind == "video":
            assert len(step["source"]["context_hash"]) == 64
            assert step["source"]["reference_media_id"]
        saved = session.get(ShotScript, shot_id)
        assert {key: getattr(saved, key) for key in original} == original
        assert session.get(Episode, episode_id).storyboard_version == storyboard_version
        assert run.usage["images"] == run.usage["videos"] == 0
        assert session.scalar(select(func.count(AsyncTask.id))) == 0


@pytest.mark.parametrize(
    "kind,target_kind", [("image", "asset"), ("image", "shot"), ("video", "shot")]
)
def test_null_media_parameters_inherit_complete_saved_defaults(workspace, kind, target_kind):
    factory, project_id, episode_id, _ = workspace
    conversation_id, decision_id = setup(factory, project_id, episode_id)
    model_id = media_model(factory, kind)
    with factory.begin() as session:
        session.get(Episode, episode_id).aspect = "9:16"
    target_id = (
        asset(factory, project_id, episode_id)
        if target_kind == "asset"
        else shot(factory, project_id, episode_id, reference=kind == "video")
    )
    parameters = {"target_kind": target_kind, "aspect": None, "resolution": None}
    expected = {"target_kind": target_kind, "aspect": "9:16", "resolution": "2K"}
    if target_kind == "shot" and kind == "image":
        parameters["layout"] = None
        expected.update(resolution="4K", layout="nine")
    if kind == "video":
        parameters["duration_ms"] = None
        expected.update(resolution="720p", duration_ms=3000)
    accepted = send(
        factory,
        conversation_id,
        decision_id,
        mode="generate",
        task={
            "kind": kind,
            "target_id": str(target_id),
            "model_config_id": str(model_id),
            "instructions": "Generate with inherited settings",
            "parameters": parameters,
        },
    )
    with factory.begin() as session:
        step = session.get(AgentRun, int(accepted.run.id)).checkpoint["authorization"]["steps"][0]
        references = [step["source"]["reference_media_id"]] if kind == "video" else []
        assert step["parameters"] == {**expected, "reference_media_ids": references}
        assert None not in step["parameters"].values()
        assert session.scalar(select(func.count(AsyncTask.id))) == 0
        assert session.scalar(select(func.count(AIGenerationRecord.id))) == 0


@pytest.mark.parametrize("target_kind", ["asset", "shot"])
def test_image_freeze_includes_and_deduplicates_implicit_and_extra_references(
    workspace, target_kind
):
    factory, project_id, episode_id, _ = workspace
    conversation_id, decision_id = setup(factory, project_id, episode_id)
    model_id = media_model(factory, "image")
    asset_id = asset(factory, project_id, episode_id)
    target_id = asset_id if target_kind == "asset" else shot(factory, project_id, episode_id)
    with factory.begin() as session:
        references = [
            MediaFile(
                id=next_id(),
                project_id=project_id,
                format_code="image/png",
                storage_locator=f"minio://images/frozen-reference-{next_id()}.png",
            )
            for _ in range(4)
        ]
        session.add_all(references)
        session.flush()
        reference_ids = [str(row.id) for row in references]
        saved_asset = session.get(Asset, asset_id)
        if target_kind == "asset":
            saved_asset.reference_media_ids = [reference_ids[0], reference_ids[1], reference_ids[0]]
        else:
            saved_asset.media_id, saved_asset.state = references[0].id, "confirmed"
            unconfirmed = Asset(
                id=next_id(),
                project_id=project_id,
                kind="prop",
                name="Unconfirmed prop",
                media_id=references[3].id,
            )
            session.add(unconfirmed)
            session.flush()
            session.add_all(
                [
                    ShotAsset(
                        id=next_id(), episode_id=episode_id, shot_id=target_id, asset_id=row.id
                    )
                    for row in (saved_asset, unconfirmed)
                ]
            )
            session.get(ShotScript, target_id).reference_media_ids = [
                reference_ids[1],
                reference_ids[0],
            ]
    accepted = send(
        factory,
        conversation_id,
        decision_id,
        mode="generate",
        task={
            "kind": "image",
            "target_id": str(target_id),
            "model_config_id": str(model_id),
            "instructions": "Generate from the approved complete references",
            "parameters": {
                "target_kind": target_kind,
                "reference_media_ids": [reference_ids[1], reference_ids[2], reference_ids[0]],
            },
        },
    )
    with factory.begin() as session:
        step = session.get(AgentRun, int(accepted.run.id)).checkpoint["authorization"]["steps"][0]
        assert step["parameters"]["reference_media_ids"] == reference_ids[:3]
        assert reference_ids[3] not in step["parameters"]["reference_media_ids"]
        assert session.scalar(select(func.count(AsyncTask.id))) == 0


def test_video_adopted_reference_cannot_be_overridden_by_user(workspace):
    factory, _, _, _ = workspace
    conversation_id, decision_id, payload = native_scope(workspace, "video")
    payload["parameters"] = {"reference_media_ids": [str(next_id())]}
    response = message_client(factory).post(
        f"/api/v1/agent/conversations/{conversation_id}/messages",
        json={
            "content": "Generate using a different reference",
            "mode": "generate",
            "model_config_id": str(decision_id),
            "task": payload,
        },
        headers={"Idempotency-Key": "override-video-reference"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_agent_parameters"
    with factory.begin() as session:
        for model in (AgentRun, AgentMessage, AgentTurn, AsyncTask, AIGenerationRecord):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.get(AgentConversation, conversation_id).next_message_seq == 1


def test_episode_context_exposes_only_owner_models_and_current_episode_targets(workspace):
    factory, project_id, episode_id, _ = workspace
    own_conversation, _ = setup(factory, project_id, episode_id)
    member_conversation, _ = setup(factory, project_id, episode_id, owner=2)
    own_image = media_model(factory, "image", name="My image model")
    own_video = media_model(factory, "video", name="My video model")
    other_image = media_model(factory, "image", owner=2, name="Other member model")
    media_model(factory, "image", enabled=0, name="Disabled own model")
    media_model(factory, "video", deleted=1, name="Deleted own model")
    current_asset = asset(factory, project_id, episode_id, name="Current episode hero")
    current_shot = shot(factory, project_id, episode_id)
    with factory() as session:
        session.info["actor"] = actor(1)
        other_episode = EpisodeService(session).create_for_project(
            project_id, {"title": "Other episode"}
        )
    other_asset = asset(
        factory, project_id, other_episode.id, name="Other episode asset", position=2
    )
    other_shot = shot(factory, project_id, other_episode.id)
    with factory.begin() as session:
        now = utcnow()
        archived = ShotScript(
            id=next_id(),
            episode_id=episode_id,
            position=2,
            script="Archived shot",
            created_at=now,
            updated_at=now,
            deleted_at=now,
        )
        session.add(archived)
        archived_id = archived.id
    # Worker sessions do not rely on request loader criteria. The tool's explicit
    # owner/project/episode filters must enforce these boundaries on their own.
    with factory.begin() as session:
        owner_context = _read_context(
            session, session.get(AgentConversation, own_conversation), {"kind": "episode"}
        )
        member_context = _read_context(
            session, session.get(AgentConversation, member_conversation), {"kind": "episode"}
        )
        with pytest.raises(ValueError, match="scope"):
            _read_context(
                session,
                session.get(AgentConversation, own_conversation),
                {"kind": "episode", "id": str(other_episode.id)},
            )
    assert owner_context["media_models"] == [
        {"id": str(own_image), "name": "My image model", "kind": "image"},
        {"id": str(own_video), "name": "My video model", "kind": "video"},
    ]
    assert member_context["media_models"] == [
        {"id": str(other_image), "name": "Other member model", "kind": "image"}
    ]
    assert owner_context["targets"]["assets"] == [
        {
            "id": str(current_asset),
            "name": "Current episode hero",
            "kind": "character",
            "row_version": 1,
        }
    ]
    assert [item["id"] for item in owner_context["targets"]["shots"]] == [str(current_shot)]
    assert owner_context["targets"]["limit_per_kind"] == 100
    serialized = json.dumps(owner_context)
    for forbidden in (
        str(other_asset),
        str(other_shot),
        str(archived_id),
        "PRIVATE_KEY_ENVELOPE",
        "private-native-endpoint",
        "apikey",
        "base_url",
        "credential",
        "model_snapshot",
    ):
        assert forbidden not in serialized
    assert all(set(item) == {"id", "name", "kind"} for item in owner_context["media_models"])
