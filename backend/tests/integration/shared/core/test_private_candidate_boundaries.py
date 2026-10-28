"""真实 MySQL 所有者采用作品及非所有者隔离；媒体 URL 使用测试替身。"""

from types import SimpleNamespace

import pytest

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    MediaAsset,
    MediaFile,
    ProjectAssembly,
    ProjectSound,
    User,
)
from short_drama.schemas.creation.sound.tracks.sound import SoundDocument
from short_drama.schemas.generations.admission.generation import VideoGenerationCreate
from short_drama.service.base import utcnow
from short_drama.service.creation.sound.tracks.sound import ProjectSoundService, line_hash
from short_drama.service.creation.storyboard.management.storyboard import ProjectStoryboardService
from short_drama.service.media.library.assets import MediaAssetService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration


@pytest.fixture
def workspace(db_session, mysql_engine):
    from short_drama.service.projects.management.project import ProjectService
    from tests.support.integration.identity import session_factory

    with db_session.begin():
        db_session.add(
            User(
                id=2,
                username="other",
                display_name="Other",
                email="other@example.test",
                password_hash="test-only",
                status="active",
                created_at=utcnow(),
            )
        )
    project = ProjectService(db_session).create_project({"name": "Owner privacy"})
    return session_factory(mysql_engine), project.id


def actor(identifier):
    return SimpleNamespace(user_id=identifier, request_id="mysql-owner-privacy")


def media_model(factory, service_type):
    from short_drama.service.ai_config.management.models import AIModelConfigService

    with factory() as session:
        return (
            AIModelConfigService(session)
            .create(
                {
                    "service_type": service_type,
                    "name": "Model",
                    "model_key": "m",
                    "provider": "test",
                }
            )
            .id
        )


def shot(factory, project_id):
    from short_drama.service.creation.storyboard.management.script import ShotScriptService

    with factory() as session:
        return (
            ShotScriptService(session)
            .create({"project_id": project_id, "position": 1, "script": "Owner scene"})
            .id
        )


def test_owner_adopts_video_and_nonowner_cannot_read_any_generation_layer(workspace):
    factory, project_id = workspace
    shot_id = shot(factory, project_id)
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
        current = ProjectStoryboardService(owner).get(project_id, shot_id)["shot"]
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
        current = ProjectStoryboardService(owner).get(project_id, shot_id)["shot"]
        assert current["video"]["first_frame_media_id"] == str(first_id)
    with factory() as member:
        member.info["actor"] = actor(2)
        with pytest.raises(BusinessError) as blocked:
            ProjectStoryboardService(member).get(project_id, shot_id)
        assert blocked.value.status_code == 404
        with member.begin():
            assert member.get(MediaFile, output_id) is None
            assert member.get(MediaFile, first_id) is None
            assert member.get(MediaAsset, candidate_id) is None
            assert member.get(AIGenerationRecord, record_id) is None


def test_owner_adopts_dialogue_and_nonowner_cannot_read_work_or_candidate(workspace):
    factory, project_id = workspace
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
        assembly = ProjectAssembly(
            id=next_id(),
            project_id=project_id,
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
                    "project_id": str(project_id),
                    "line_id": line["id"],
                },
                "source_snapshot": {"line_hash": line_hash(line)},
            },
            response_data={},
            status="succeeded",
            created_at=now,
            updated_at=now,
        )
        sound = ProjectSound(
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
        service = ProjectSoundService(owner, settings, storage)
        adopted = service.adopt(project_id, adoption)
        assert adopted["document"]["dialogue"][0]["media_id"] == str(media_id)
        assert service.adopt(project_id, adoption)["row_version"] == "2"
    with factory.begin() as system:
        assert system.get(MediaFile, media_id).published_at is not None
    with factory() as member:
        member.info["actor"] = actor(2)
        with pytest.raises(BusinessError) as blocked:
            ProjectSoundService(member, settings, storage).get(project_id)
        assert blocked.value.status_code == 404
        with member.begin():
            assert member.get(MediaFile, media_id) is None
            assert member.get(MediaAsset, candidate_id) is None
            assert member.get(AIGenerationRecord, record_id) is None
