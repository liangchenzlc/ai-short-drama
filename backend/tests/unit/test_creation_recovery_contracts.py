from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.agent import state
from short_drama.domain import AgentEvent, AgentMessage
from short_drama.schemas.agent import ConversationPatch
from short_drama.schemas.agent_artifacts import ArtifactAdopt
from short_drama.schemas.agent_runtime import ReviewDecision, RunContinue
from short_drama.schemas.episode_sound import SoundAdopt, SoundEdit, VoiceDefaultsEdit
from short_drama.service.agent_artifact_service import AgentArtifactService


def artifact(*, source_content="原始生成稿"):
    now = datetime(2026, 10, 4)
    return SimpleNamespace(
        id=1,
        project_id=2,
        episode_id=3,
        kind="script_candidate",
        status="applied",
        row_version=9007199254740993,
        source_content=source_content,
        source_snapshot={
            "episode_id": 3,
            "content_version": 9007199254740993,
            "storyboard_version": 1,
            "episode_row_version": 1,
        },
        script_id=4,
        parent_script_id=None,
        generation_task_id=None,
        media_asset_id=None,
        media_id=None,
        target_asset_id=None,
        target_shot_id=None,
        created_by=5,
        applied_by=5,
        applied_at=now,
        created_at=now,
        updated_at=now,
        proposed_patch=None,
        metadata_json={},
        apply_receipt={"action": "select_script", "content_version": 9007199254740994},
    )


def test_original_agent_script_is_immutable_and_legacy_origin_is_explicit():
    service = AgentArtifactService(None)
    record = artifact()
    script = SimpleNamespace(content="采用后修改的正文")
    projected = service._view(record, detail=True, script=script)
    assert projected["content"] == "原始生成稿"
    assert projected["content_origin"] == "snapshot"
    assert projected["row_version"] == "9007199254740993"
    assert projected["source_snapshot"]["content_version"] == "9007199254740993"
    assert projected["apply_receipt"]["content_version"] == "9007199254740994"
    record.source_content = None
    legacy = service._view(record, detail=True, script=script)
    assert legacy["content"] == script.content
    assert legacy["content_origin"] == "current_script_legacy"


@pytest.mark.parametrize("version", ["9007199254740993", 9007199254740993])
def test_agent_and_sound_versions_accept_legacy_int_but_serialize_decimal_string(version):
    inputs = [
        ConversationPatch(row_version=version, title="新标题"),
        ArtifactAdopt(
            row_version=version,
            content_version=version,
            storyboard_version=version,
            target_row_version=version,
        ),
        ReviewDecision(review_version=version, review_hash="a" * 64, decision="approved"),
        RunContinue(artifact_id="1", artifact_row_version=version),
        SoundAdopt(row_version=version, line_id="line", media_id="1"),
        VoiceDefaultsEdit(row_version=version, voices={}),
    ]
    for model in inputs:
        for key, value in model.model_dump(mode="json").items():
            if key.endswith("_version"):
                assert value == "9007199254740993"
    initial = SoundEdit(row_version="0", timeline_hash="a" * 64, request_id="save", document={})
    assert initial.model_dump(mode="json")["row_version"] == "0"
    for invalid in [True, "-1", "18446744073709551616", 1.5]:
        with pytest.raises(ValidationError):
            VoiceDefaultsEdit(row_version=invalid, voices={})


def test_late_results_are_visible_without_resuming_a_stopped_run(monkeypatch):
    conversation = SimpleNamespace(id=1, next_message_seq=9, next_event_seq=10)
    run = SimpleNamespace(id=2, trigger_message_id=3, status="cancelled", next_run_at=None)
    monkeypatch.setattr(
        state,
        "run_artifact_references",
        lambda *_: [
            {"artifact_id": "11", "kind": "image_candidate"},
            {"artifact_id": "12", "kind": "video_candidate"},
        ],
    )
    inserted = []
    state.append_late_artifact_message(
        SimpleNamespace(add=inserted.append, scalars=lambda *_: SimpleNamespace(all=lambda: [])),
        conversation,
        run,
        {"12"},
    )
    message = next(row for row in inserted if isinstance(row, AgentMessage))
    event = next(row for row in inserted if isinstance(row, AgentEvent))
    assert message.artifacts == [{"artifact_id": "12", "kind": "video_candidate"}]
    assert event.payload["artifacts"] == message.artifacts
    assert "不会自动继续" in message.content
    assert run.status == "cancelled" and run.next_run_at is None
