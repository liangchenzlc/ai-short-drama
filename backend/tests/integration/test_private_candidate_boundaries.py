"""真实 MySQL 三成员隐私与公开作品投影；媒体 URL 使用测试替身。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_agent_conversations import workspace as workspace
from test_agent_data_layer import actor, seed
from test_agent_native_parameters import media_model, shot

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound
from short_drama.domain import (
    AgentArtifact,
    AIGenerationRecord,
    Asset,
    AssetImageCandidate,
    AsyncTask,
    EpisodeAssembly,
    EpisodeRenderJob,
    EpisodeScript,
    EpisodeSound,
    MediaAsset,
    MediaFile,
    ProjectMember,
    User,
)
from short_drama.schemas.ai_generation import VideoGenerationCreate
from short_drama.schemas.episode_sound import SoundDocument
from short_drama.service.base import utcnow
from short_drama.service.episode_assembly_service import EpisodeAssemblyService
from short_drama.service.episode_sound_service import EpisodeSoundService, line_hash
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.episode_writing_service import EpisodeWritingService
from short_drama.service.media_asset_service import MediaAssetService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration


def test_adopted_video_is_shared_without_private_generation_or_first_frame(workspace):
    factory, project_id, episode_id, _ = workspace
    shot_id = shot(factory, project_id, episode_id)
    model_id = media_model(factory, "video")
    now = utcnow()
    with factory.begin() as system:
        first = MediaFile(
            id=next_id(),
            project_id=project_id,
            created_by=1,
            format_code="image/png",
            storage_locator="minio://images/private-frame.png",
        )
        output = MediaFile(
            id=next_id(),
            project_id=project_id,
            created_by=1,
            format_code="video/mp4",
            storage_locator="minio://videos/adopted-video.mp4",
            duration_ms=1000,
        )
        task = AsyncTask(
            id=next_id(),
            project_id=project_id,
            initiated_by=1,
            service_type="video",
            status="succeeded",
            idempotency_key="private-video-adoption",
            request_hash="0" * 64,
            next_action="save",
            created_at=now,
            updated_at=now,
            finished_at=now,
        )
        system.add_all([first, output, task])
        system.flush()
        request = VideoGenerationCreate(
            project_id=project_id,
            config_id=model_id,
            input={"prompt": "Private original frame", "first_frame_media_id": first.id},
            parameters={"resolution": "720p", "duration_ms": 1000},
        ).model_dump(mode="json")
        record = AIGenerationRecord(
            id=next_id(),
            task_id=task.id,
            call_no=1,
            config_id=model_id,
            config_snapshot={},
            request_data=request,
            response_data={},
            status="succeeded",
            created_at=now,
            updated_at=now,
        )
        system.add(record)
        system.flush()
        candidate = MediaAsset(
            id=next_id(),
            record_id=record.id,
            output_index=1,
            media_id=output.id,
            media_type="video",
            name="Generated video",
            created_at=now,
            updated_at=now,
        )
        system.add(candidate)
        first_id, output_id, candidate_id, record_id = (
            first.id,
            output.id,
            candidate.id,
            record.id,
        )
    with factory() as owner:
        owner.info["actor"] = actor(1)
        current = EpisodeStoryboardService(owner).get(project_id, episode_id, shot_id)["shot"]
        MediaAssetService(owner, Settings(_env_file=None), None).apply(
            candidate_id,
            {
                "target": {"type": "shot_video", "id": str(shot_id)},
                "expected_media_id": None,
                "expected_row_version": current["row_version"],
                "expected_context_hash": current["video_context_hash"],
                "parameters": {"resolution": "720p", "duration": 1000},
                "acknowledge_stale_source": True,
            },
        )
    with factory() as owner:
        owner.info["actor"] = actor(1)
        current = EpisodeStoryboardService(owner).get(project_id, episode_id, shot_id)["shot"]
        assert current["video"]["first_frame_media_id"] == str(first_id)
    with factory() as member:
        member.info["actor"] = actor(2)
        current = EpisodeStoryboardService(member).get(project_id, episode_id, shot_id)["shot"]
        assert current["video"] is not None
        assert current["video"]["media_id"] == str(output_id)
        assert current["video"]["media_asset_id"] is None
        assert current["video"]["first_frame_media_id"] is None
        with member.begin():
            assert member.get(MediaFile, output_id) is not None
            assert member.get(MediaFile, first_id) is None
            assert member.get(MediaAsset, candidate_id) is None
            assert member.get(AIGenerationRecord, record_id) is None


def test_adopted_dialogue_audio_is_shared_without_private_model_or_candidate(workspace):
    factory, project_id, episode_id, _ = workspace
    settings = Settings(_env_file=None, audio_production_enabled=True)
    model_id = media_model(factory, "audio")
    now = utcnow()
    line = {
        "id": "shared-line",
        "character": "Hero",
        "text": "Shared dialogue",
        "voice": "Local voice",
        "config_id": str(model_id),
        "start_ms": 0,
        "media_id": None,
        "adopted_hash": None,
    }
    with factory.begin() as system:
        media = MediaFile(
            id=next_id(),
            project_id=project_id,
            created_by=1,
            format_code="audio/wav",
            storage_locator=f"minio://{settings.minio_audio_bucket}/dialogue.wav",
            duration_ms=1000,
        )
        task = AsyncTask(
            id=next_id(),
            project_id=project_id,
            initiated_by=1,
            service_type="audio",
            status="succeeded",
            idempotency_key="private-dialogue-adoption",
            request_hash="0" * 64,
            next_action="save",
            created_at=now,
            updated_at=now,
            finished_at=now,
        )
        assembly = EpisodeAssembly(
            id=next_id(),
            episode_id=episode_id,
            aspect="16:9",
            resolution="720p",
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        system.add_all([media, task, assembly])
        system.flush()
        record = AIGenerationRecord(
            id=next_id(),
            task_id=task.id,
            call_no=1,
            config_id=model_id,
            config_snapshot={},
            request_data={
                "source": {
                    "scene": "dialogue_audio",
                    "episode_id": str(episode_id),
                    "line_id": line["id"],
                },
                "source_snapshot": {"line_hash": line_hash(line)},
            },
            response_data={},
            status="succeeded",
            created_at=now,
            updated_at=now,
        )
        sound = EpisodeSound(
            assembly_id=assembly.id,
            row_version=1,
            document=SoundDocument(dialogue=[line]).model_dump(mode="json"),
            updated_at=now,
        )
        system.add_all([record, sound])
        system.flush()
        candidate = MediaAsset(
            id=next_id(),
            record_id=record.id,
            output_index=1,
            media_id=media.id,
            media_type="audio",
            name="Private dialogue candidate",
            created_at=now,
            updated_at=now,
        )
        system.add(candidate)
        media_id, candidate_id, record_id = media.id, candidate.id, record.id
    storage = SimpleNamespace(presigned_get=lambda *_: "https://media.example.test/dialogue")
    adoption = {"row_version": 1, "line_id": line["id"], "media_id": str(media_id)}
    with factory() as owner:
        owner.info["actor"] = actor(1)
        service = EpisodeSoundService(owner, settings, storage)
        adopted = service.adopt(project_id, episode_id, adoption)
        assert adopted["document"]["dialogue"][0]["media_id"] == str(media_id)
        assert service.adopt(project_id, episode_id, adoption)["row_version"] == 2
    with factory.begin() as system:
        assert system.get(MediaFile, media_id).published_at is not None
    with factory() as member:
        member.info["actor"] = actor(2)
        shared = EpisodeSoundService(member, settings, storage).get(project_id, episode_id)
        assert shared["document"]["dialogue"][0]["media_id"] == str(media_id)
        assert shared["document"]["dialogue"][0]["config_id"] is None
        assert shared["media"][str(media_id)]["url"] is not None
        assert shared["stale_lines"] == []
        with member.begin():
            assert member.get(MediaFile, media_id) is not None
            assert member.get(MediaAsset, candidate_id) is None
            assert member.get(AIGenerationRecord, record_id) is None


def test_three_members_private_generation_layers_published_sound_and_render_work(db_session):
    settings = Settings(_env_file=None, audio_production_enabled=True)
    project, episode, model, *_ = seed(db_session)
    db_session.info.pop("actor")
    db_session.info.pop("legacy_user_id", None)
    now = utcnow()
    with db_session.begin():
        db_session.add(
            User(
                id=3,
                username="third_private_member",
                display_name="Third",
                email="third@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=now,
                created_at=now,
            )
        )
        db_session.flush()
        db_session.add(
            ProjectMember(
                id=next_id(), project_id=project.id, user_id=3, status="active", joined_at=now
            )
        )
        asset = Asset(
            id=next_id(), project_id=project.id, kind="character", name="Shared character"
        )
        db_session.add(asset)
        db_session.flush()
        identifiers = {}
        for owner in (1, 2):
            task = AsyncTask(
                id=next_id(),
                project_id=project.id,
                initiated_by=owner,
                service_type="image",
                status="succeeded",
                idempotency_key=f"private-real-{owner}",
                request_hash="0" * 64,
                next_action="save",
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
            db_session.add(task)
            db_session.flush()
            record = AIGenerationRecord(
                id=next_id(),
                task_id=task.id,
                call_no=1,
                config_id=model.id,
                config_snapshot={"private": "MODEL_SNAPSHOT"},
                request_data={"private": "FROZEN_INPUT"},
                response_data={},
                status="succeeded",
                created_at=now,
                updated_at=now,
            )
            media = MediaFile(
                id=next_id(),
                project_id=project.id,
                created_by=owner,
                format_code="image/png",
                storage_locator=f"minio://image/{next_id()}.png",
            )
            script = EpisodeScript(
                id=next_id(),
                episode_id=episode.id,
                position=owner,
                content=f"Private script {owner}",
                created_by=owner,
            )
            db_session.add_all([record, media, script])
            db_session.flush()
            output = MediaAsset(
                id=next_id(),
                record_id=record.id,
                output_index=1,
                media_id=media.id,
                media_type="image",
                name="Private output",
                created_at=now,
                updated_at=now,
            )
            candidate = AssetImageCandidate(
                id=next_id(), asset_id=asset.id, media_id=media.id, created_by=owner
            )
            db_session.add_all([output, candidate])
            identifiers[owner] = (task.id, record.id, output.id, candidate.id, script.id, media.id)
        db_session.flush()
        assembly = EpisodeAssembly(
            id=next_id(),
            episode_id=episode.id,
            aspect="16:9",
            resolution="720p",
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        movie = MediaFile(
            id=next_id(),
            project_id=project.id,
            created_by=1,
            published_at=now,
            format_code="video/mp4",
            storage_locator=f"minio://{settings.minio_video_bucket}/{next_id()}.mp4",
            width=1280,
            height=720,
            duration_ms=1000,
            video_metadata={"publication_context_hash": "0" * 64},
        )
        audio = MediaFile(
            id=next_id(),
            project_id=project.id,
            created_by=1,
            published_at=now,
            format_code="audio/wav",
            storage_locator=f"minio://{settings.minio_audio_bucket}/{next_id()}.wav",
            duration_ms=1000,
        )
        db_session.add_all([assembly, movie, audio])
        db_session.flush()
        assembly.current_media_id = movie.id
        job = EpisodeRenderJob(
            id=next_id(),
            assembly_id=assembly.id,
            initiated_by=1,
            kind="export",
            status="succeeded",
            stage="complete",
            snapshot={"private": "FROZEN_EXPORT"},
            context_hash="0" * 64,
            idempotency_key="private-real-export",
            request_hash="1" * 64,
            next_run_at=now,
            created_at=now,
            updated_at=now,
            output_media_id=movie.id,
        )
        db_session.add(job)
        line = {
            "id": "line-private",
            "character": "Hero",
            "text": "Shared dialogue",
            "voice": "Voice",
            "config_id": str(model.id),
            "start_ms": 0,
            "media_id": str(audio.id),
        }
        line["adopted_hash"] = line_hash(line)
        document = SoundDocument(dialogue=[line]).model_dump(mode="json")
        db_session.add(
            EpisodeSound(assembly_id=assembly.id, row_version=1, document=document, updated_at=now)
        )
    storage = SimpleNamespace(presigned_get=lambda *_: "https://media.example.test/shared")
    project_id, episode_id, model_id, movie_id, job_id = (
        project.id,
        episode.id,
        model.id,
        movie.id,
        job.id,
    )
    for owner in (1, 2, 3):
        db_session.info["actor"] = actor(owner)
        with db_session.begin():
            for model_type, index in (
                (AsyncTask, 0),
                (AIGenerationRecord, 1),
                (MediaAsset, 2),
                (AssetImageCandidate, 3),
                (EpisodeScript, 4),
            ):
                expected = {identifiers[owner][index]} if owner in identifiers else set()
                assert set(db_session.scalars(select(model_type.id))) == expected
            assert set(db_session.scalars(select(AgentArtifact.id))) == (
                {15} if owner == 1 else set()
            )
        writing = EpisodeWritingService(db_session).candidates(project_id, episode_id)
        assert len(writing["items"]) == (1 if owner in identifiers else 0)
        view = EpisodeAssemblyService(db_session, settings, storage).get(project_id, episode_id)
        assert view["current_work"]["media_id"] == str(movie_id)
        assert len(view["jobs"]) == (1 if owner == 1 else 0)
        if owner != 1:
            with pytest.raises(NotFound):
                EpisodeAssemblyService(db_session, settings, storage).job_action(
                    project_id, episode_id, job_id, "get"
                )
        sound = EpisodeSoundService(db_session, settings, storage).get(project_id, episode_id)
        assert sound["document"]["dialogue"][0]["config_id"] == (
            str(model_id) if owner == 1 else None
        )
        assert sound["stale_lines"] == []
        if owner == 2:
            # Saving another member's sound timing keeps the hidden internal model
            # association and the adopted hash, without disclosing either config.
            saved = EpisodeSoundService(db_session, settings, storage).save(
                project_id,
                episode_id,
                {
                    "row_version": sound["row_version"],
                    "timeline_hash": sound["timeline_hash"],
                    "request_id": "shared-sound-timing",
                    "document": sound["document"],
                },
            )
            assert saved["document"]["dialogue"][0]["config_id"] is None
            assert saved["stale_lines"] == []
    db_session.info.pop("actor")
    with db_session.begin():
        db_session.get(EpisodeScript, identifiers[1][4]).published_at = now
        db_session.get(MediaFile, identifiers[1][5]).published_at = now
    db_session.info["actor"] = actor(3)
    with db_session.begin():
        assert set(db_session.scalars(select(EpisodeScript.id))) == {identifiers[1][4]}
        assert set(db_session.scalars(select(AsyncTask.id))) == set()
        assert set(db_session.scalars(select(AIGenerationRecord.id))) == set()
        assert set(db_session.scalars(select(MediaAsset.id))) == set()
        assert set(db_session.scalars(select(AssetImageCandidate.id))) == set()
    assert EpisodeWritingService(db_session).candidates(project_id, episode_id)["items"] == []
