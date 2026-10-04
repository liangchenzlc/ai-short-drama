"""私有执行层与已采用业务作品分开授权；不调用真实供应商。"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from generation_fixtures import config, generation_session
from sqlalchemy import select
from test_episode_writing import setup

import short_drama.db.access  # noqa: F401
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    AgentArtifact,
    AIGenerationRecord,
    Asset,
    AssetImageCandidate,
    AsyncTask,
    Episode,
    EpisodeNovel,
    EpisodeScript,
    MediaAsset,
    MediaFile,
    NovelScriptRecord,
    ProjectMember,
    User,
)
from short_drama.domain.collaboration import AuditEvent
from short_drama.service.base import utcnow


def actor(session, user_id):
    session.info["actor"] = SimpleNamespace(user_id=user_id, request_id="candidate-privacy")


def seed(session):
    project_id, episode_id, writing = setup(session)
    config(session)
    session.info.pop("legacy_user_id")
    connection = session.connection().connection.driver_connection
    connection.create_function("JSON_OBJECT", 0, lambda: "{}")
    now = utcnow()
    for user_id in (2, 3):
        session.add(
            User(
                id=user_id,
                username=f"member{user_id}",
                display_name="Member",
                email=f"member{user_id}@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=now,
                created_at=now,
            )
        )
        session.add(
            ProjectMember(
                id=10 + user_id,
                project_id=project_id,
                user_id=user_id,
                status="active",
                joined_at=now,
            )
        )
    session.add(Asset(id=50, project_id=project_id, kind="character", name="Hero"))
    session.add(EpisodeNovel(id=60, episode_id=episode_id, content="Novel"))
    for index, owner in enumerate((1, 2, None)):
        session.add(
            AsyncTask(
                id=100 + index,
                project_id=project_id,
                initiated_by=owner,
                service_type="text",
                status="succeeded",
                idempotency_key=f"private-{index}",
                request_hash="0" * 64,
                next_action="save",
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
        )
        session.add(
            AIGenerationRecord(
                id=200 + index,
                task_id=100 + index,
                call_no=1,
                config_id=1,
                config_snapshot={"private": "private-model"},
                request_data={
                    "source": {
                        "scene": "novel_script",
                        "episode_id": str(episode_id),
                        "novel_id": "60",
                    }
                },
                response_data={},
                status="succeeded",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            MediaFile(
                id=300 + index,
                project_id=project_id,
                created_by=owner,
                format_code="image/png",
                storage_locator=f"mock:private:{index}",
            )
        )
        session.add(
            MediaAsset(
                id=400 + index,
                record_id=200 + index,
                output_index=1,
                media_id=300 + index,
                media_type="image",
                name="Candidate",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            EpisodeScript(
                id=500 + index,
                episode_id=episode_id,
                position=index + 1,
                content=f"Private {index}",
                created_by=owner,
            )
        )
        session.add(
            NovelScriptRecord(
                id=600 + index,
                novel_id=60,
                script_id=500 + index,
                batch_id=100 + index,
                created_by=owner,
            )
        )
        session.add(
            AssetImageCandidate(id=700 + index, asset_id=50, media_id=300 + index, created_by=owner)
        )
        if owner:
            session.add(
                AuditEvent(
                    id=900 + index,
                    actor_user_id=owner,
                    project_id=project_id,
                    object_type="novel_script_records",
                    object_id=str(600 + index),
                    action="create",
                    request_id="private-provenance-audit",
                    created_at=now,
                )
            )
            session.add(
                AgentArtifact(
                    id=800 + index,
                    project_id=project_id,
                    episode_id=episode_id,
                    tool_call_id=900 + index,
                    result_index=1,
                    kind="script_candidate",
                    script_id=500 + index,
                    created_by=owner,
                )
            )
    session.commit()
    return project_id, episode_id, writing


def test_three_members_only_see_own_execution_and_candidates_after_publication():
    with generation_session() as session:
        project_id, episode_id, writing = seed(session)
        with session.begin():
            session.get(EpisodeScript, 500).published_at = utcnow()
            session.get(MediaFile, 300).published_at = utcnow()
        for user_id, offset in ((1, 0), (2, 1), (3, None)):
            actor(session, user_id)
            with session.begin():
                for model, base in (
                    (AsyncTask, 100),
                    (AIGenerationRecord, 200),
                    (MediaAsset, 400),
                    (NovelScriptRecord, 600),
                    (AssetImageCandidate, 700),
                    (AgentArtifact, 800),
                ):
                    assert list(session.scalars(select(model.id))) == (
                        [] if offset is None else [base + offset]
                    )
                expected_scripts = {500} | ({500 + offset} if offset is not None else set())
                assert set(session.scalars(select(EpisodeScript.id))) == expected_scripts
                expected_media = {300} | ({300 + offset} if offset is not None else set())
                assert set(session.scalars(select(MediaFile.id))) == expected_media
                assert list(session.scalars(select(AuditEvent.id))) == (
                    [] if offset is None else [900 + offset]
                )
            assert [row["id"] for row in writing.candidates(project_id, episode_id)["items"]] == (
                [] if offset is None else [str(500 + offset)]
            )


@pytest.mark.parametrize(
    "model,identifier,field,value",
    [
        (AsyncTask, 100, "cancel_requested", 1),
        (EpisodeScript, 500, "content", "Stolen candidate"),
        (MediaFile, 300, "original_name", "Stolen media"),
        (AgentArtifact, 800, "status", "discarded"),
    ],
)
def test_cached_private_entities_cannot_be_mutated_by_another_member(
    model, identifier, field, value
):
    with generation_session() as session:
        seed(session)
        entity = session.get(model, identifier)
        session.rollback()
        actor(session, 2)
        with pytest.raises(NotFound), session.begin():
            setattr(entity, field, value)
            session.flush()


def test_published_work_cannot_be_unpublished_and_revocation_hides_it():
    with generation_session() as session:
        seed(session)
        with session.begin():
            session.get(MediaFile, 300).published_at = utcnow()
        actor(session, 1)
        with pytest.raises(WorkflowError, match="unpublished"), session.begin():
            session.get(MediaFile, 300).published_at = None
            session.flush()
        session.info.pop("actor")
        with session.begin():
            session.get(ProjectMember, 12).status = "removed"
        actor(session, 2)
        with session.begin():
            assert list(session.scalars(select(MediaFile.id))) == []
            assert list(session.scalars(select(AsyncTask.id))) == []


def migration():
    path = Path(__file__).resolve().parents[2] / "scripts" / "backfill_private_candidates.py"
    spec = importlib.util.spec_from_file_location("private_candidate_backfill", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_proves_exact_authors_keeps_unknown_candidates_and_publishes_only_work():
    with generation_session() as session:
        _project_id, episode_id, _writing = seed(session)
        with session.begin():
            for model in (EpisodeScript, NovelScriptRecord, MediaFile, AssetImageCandidate):
                for row in session.scalars(select(model)):
                    row.created_by = None
            session.get(Episode, episode_id).editing_script_id = 500
            # An unrelated task cannot claim a script in another source episode.
            row = session.get(AIGenerationRecord, 201)
            row.request_data = {
                "source": {"scene": "novel_script", "episode_id": "999", "novel_id": "60"}
            }
            session.delete(session.get(AgentArtifact, 801))
            session.delete(session.get(AgentArtifact, 800))
        with session.begin():
            result = migration().backfill(session)
            assert session.get(EpisodeScript, 500).created_by == 1
            assert session.get(EpisodeScript, 501).created_by is None
            assert session.get(EpisodeScript, 502).created_by is None
            assert session.get(MediaFile, 300).created_by == 1
            assert session.get(MediaFile, 301).created_by == 2
            assert session.get(MediaFile, 302).created_by is None
            assert session.get(EpisodeScript, 500).published_at
            assert session.get(EpisodeScript, 501).published_at is None
            assert all(row.published_at is None for row in session.scalars(select(MediaFile)))
            assert all(
                row.created_by is None for row in session.scalars(select(AssetImageCandidate))
            )
            assert result["script_authors"] == 1
        with session.begin():
            repeated = migration().backfill(session)
            assert not any(not key.startswith("quarantined_") for key in repeated)


def test_backfill_rejects_uncertain_or_active_task_without_changing_work():
    with generation_session() as session:
        seed(session)
        with session.begin():
            record = session.get(AIGenerationRecord, 200)
            record.status = "unknown"
        with pytest.raises(ValueError, match="活动"), session.begin():
            migration().backfill(session)
        with session.begin():
            assert session.get(MediaFile, 300).published_at is None
