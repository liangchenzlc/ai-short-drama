"""Native shared adoption uses the original review, atomically and across members."""

import json
from copy import deepcopy

import pytest
import test_agent_conversations as conversation_tests
from sqlalchemy import func, select
from test_agent_artifacts import adopt_body
from test_agent_conversations import actor
from test_agent_native_tasks import Provider, admit, drain, executor, prepared

from short_drama.agent.native_tasks import collect_native_results
from short_drama.ai import GenerationResult
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    AgentArtifact,
    AIGenerationRecord,
    Asset,
    Episode,
    EpisodeAsset,
    ShotScript,
    ShotVideo,
)
from short_drama.service.agent_artifact_service import AgentArtifactService
from short_drama.service.episode_writing_service import EpisodeWritingService

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


class ExtractionProvider(Provider):
    def result(self, adapter, count=1):
        return GenerationResult(
            status="succeeded",
            adapter=adapter,
            text=json.dumps(
                {
                    "schema_version": 1,
                    "items": [
                        {
                            "kind": "character",
                            "name": "林晚",
                            "description": "年轻女性，蓝色外套。",
                            "prompt": "Young woman in a blue coat.",
                            "scene_time": "",
                            "aliases": [],
                            "importance": "core",
                            "story_function": "主角，走进老宅。",
                        }
                    ],
                }
            ),
            finish_reason="stop",
        )


def complete(workspace, kind):
    flow = prepared(workspace, kind)
    task_id = admit(flow)
    provider = ExtractionProvider(kind) if kind == "extract" else Provider(kind)
    assert drain(flow, task_id, executor(flow, provider)) == "succeeded"
    assert collect_native_results(flow.factory, flow.settings) == 1
    with flow.factory() as session:
        identifier = session.scalar(select(AgentArtifact.id))
        session.rollback()
        session.info["actor"] = actor(2)
        with pytest.raises(NotFound):
            AgentArtifactService(session, flow.settings).get(
                flow.project_id, flow.episode_id, identifier
            )
        session.info["actor"] = actor(1)
        detail = AgentArtifactService(session, flow.settings).get(
            flow.project_id, flow.episode_id, identifier
        )
    return flow, identifier, detail


def adopt(flow, identifier, body):
    with flow.factory() as session:
        session.info["actor"] = actor(1)
        return AgentArtifactService(session, flow.settings).adopt(
            flow.project_id, flow.episode_id, identifier, body
        )


@pytest.mark.parametrize("kind", ["extract", "storyboard"])
def test_native_text_requires_owner_review_then_applies_and_replays(workspace, kind):
    flow, identifier, detail = complete(workspace, kind)
    body = adopt_body(detail)
    with pytest.raises(WorkflowError) as missing:
        adopt(flow, identifier, body)
    assert missing.value.code == "agent_native_review_required"
    with pytest.raises(WorkflowError) as invalid:
        adopt(flow, identifier, {**body, "native_review": {"unknown": True}})
    assert invalid.value.code == "invalid_agent_native_review"
    with flow.factory() as session:
        record = session.scalar(select(AIGenerationRecord))
        result = deepcopy(record.response_data["business_result"])
    body["native_review"] = (
        {
            "content_version": body["content_version"],
            "result_version": result["result_version"],
            "items": [{"candidate_id": result["items"][0]["candidate_id"], "action": "create"}],
        }
        if kind == "extract"
        else {
            "mode": "append",
            "content_version": body["content_version"],
            "storyboard_version": body["storyboard_version"],
        }
    )
    applied = adopt(flow, identifier, body)
    assert applied["status"] == "applied" and applied["applied_by"] == "1"
    assert adopt(flow, identifier, body)["apply_receipt"] == applied["apply_receipt"]
    with flow.factory() as session:
        if kind == "extract":
            assert session.scalar(select(func.count(EpisodeAsset.id))) == 1
            asset = session.scalar(select(Asset))
            assert asset.state == "unconfirmed" and asset.media_id is None
        else:
            assert session.scalar(select(func.count(ShotScript.id))) == 1
            assert session.get(Episode, flow.episode_id).storyboard_version == 2


@pytest.mark.parametrize("kind", ["image", "video"])
def test_private_media_uses_frozen_source_and_owner_adoption_shares_work(workspace, kind):
    flow, identifier, detail = complete(workspace, kind)
    body = {**adopt_body(detail), "confirm_shared": True}
    with pytest.raises(WorkflowError) as unconfirmed:
        adopt(flow, identifier, {**body, "confirm_shared": False})
    assert unconfirmed.value.code == "shared_confirmation_required"
    applied = adopt(flow, identifier, body)
    assert applied["status"] == "applied"
    assert adopt(flow, identifier, body)["apply_receipt"] == applied["apply_receipt"]
    with flow.factory() as session:
        if kind == "image":
            assert session.get(Asset, flow.target_id).media_id == int(detail["media_id"])
        else:
            assert session.scalar(select(ShotVideo.media_id)) == int(detail["media_id"])
            assert (
                session.get(ShotScript, flow.target_id).video_settings == flow.editor_video_settings
            )


@pytest.mark.parametrize("kind", ["extract", "storyboard"])
def test_native_apply_and_agent_receipt_roll_back_together(workspace, kind, monkeypatch):
    flow, identifier, detail = complete(workspace, kind)
    body = adopt_body(detail)
    with flow.factory() as session:
        result = deepcopy(
            session.scalar(select(AIGenerationRecord)).response_data["business_result"]
        )
    body["native_review"] = (
        {
            "content_version": body["content_version"],
            "result_version": result["result_version"],
            "items": [{"candidate_id": result["items"][0]["candidate_id"], "action": "create"}],
        }
        if kind == "extract"
        else {
            "mode": "append",
            "content_version": body["content_version"],
            "storyboard_version": body["storyboard_version"],
        }
    )
    original = AgentArtifactService._view

    def fault(self, artifact, **kwargs):
        if artifact.status == "applied":
            raise RuntimeError("Receipt delivery failure")
        return original(self, artifact, **kwargs)

    monkeypatch.setattr(AgentArtifactService, "_view", fault)
    with pytest.raises(RuntimeError, match="Receipt delivery"):
        adopt(flow, identifier, body)
    with flow.factory() as session:
        artifact = session.get(AgentArtifact, identifier)
        assert artifact.status == "ready" and artifact.apply_receipt is None
        assert session.scalar(select(AIGenerationRecord)).response_data["business_result"] == result
        assert session.scalar(select(func.count(EpisodeAsset.id))) == 0
        assert session.scalar(select(func.count(ShotScript.id))) == 0


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("changed", ["content", "target", "storyboard"])
def test_agent_media_stale_source_cannot_be_acknowledged_into_the_current_work(
    workspace, kind, changed
):
    flow, identifier, detail = complete(workspace, kind)
    with flow.factory() as session:
        original_media = (
            session.get(Asset, flow.target_id).media_id
            if kind == "image"
            else session.scalar(select(ShotVideo.media_id))
        )
    with flow.factory() as session:
        session.info["actor"] = actor(2)
        if changed == "content":
            EpisodeWritingService(session).save_novel(
                flow.project_id,
                flow.episode_id,
                {
                    "content_version": detail["source_snapshot"]["content_version"],
                    "content": "New work",
                },
            )
        else:
            with session.begin():
                if changed == "target":
                    target = session.get(Asset if kind == "image" else ShotScript, flow.target_id)
                    target.row_version += 1
                else:
                    session.get(Episode, flow.episode_id).storyboard_version += 1
    with flow.factory() as session:
        episode = session.get(Episode, flow.episode_id)
        target = session.get(Asset if kind == "image" else ShotScript, flow.target_id)
        body = {
            **adopt_body(detail),
            "content_version": episode.content_version,
            "storyboard_version": episode.storyboard_version,
            "target_row_version": target.row_version,
            "confirm_shared": True,
        }
    # A storyboard change cannot stale an asset image because it has no shot scope.
    if changed == "storyboard" and kind == "image":
        assert adopt(flow, identifier, body)["status"] == "applied"
        return
    with pytest.raises(WorkflowError) as stale:
        adopt(flow, identifier, body)
    assert stale.value.code == "agent_source_changed"
    with flow.factory() as session:
        artifact = session.get(AgentArtifact, identifier)
        assert artifact.status == "ready" and artifact.apply_receipt is None
        if kind == "image":
            assert session.get(Asset, flow.target_id).media_id == original_media
        else:
            assert session.scalar(select(ShotVideo.media_id)) == original_media
