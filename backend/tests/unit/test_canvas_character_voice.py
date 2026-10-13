"""已采用样音的共享投影与本人生成冻结；不调用供应商或对象存储。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.collaboration import ProjectMember
from short_drama.domain.native_voice import CharacterVoice
from short_drama.schemas.canvas_task_runtime import CanvasTaskMetadata, CanvasTaskReference
from short_drama.service.base import utcnow
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService
from short_drama.service.canvas_character_generation import freeze_canvas_characters
from short_drama.service.canvas_service import CanvasService
from tests.catalog_fixtures import ASSET, CANVAS, KEY, MEDIA, OWNING, SOURCE, actor
from tests.unit.test_canvas_character_publications import document
from tests.unit.test_canvas_character_publications import sqlite_mutex_only as sqlite_mutex_only

pytest_plugins = ("tests.catalog_fixtures",)
SETTINGS = SimpleNamespace(
    native_video_enabled=True,
    audio_production_enabled=True,
    minio_image_bucket="fixture-only",
    minio_video_bucket="fixture-video",
    minio_audio_bucket="fixture-audio",
)
SAMPLE, RECORD, TASK, CONFIG = MEDIA + 100, MEDIA + 101, MEDIA + 102, MEDIA + 103


def seed_voice(baseline, *, project_id=SOURCE, asset_id=ASSET):
    now = utcnow()
    with baseline.begin():
        baseline.add(
            AIModelConfig(
                id=CONFIG,
                owner_user_id=1,
                service_type="audio",
                name="私人模型",
                model_key="fixture",
                provider="test",
                base_url="",
                enabled=1,
                is_deleted=0,
                is_default=0,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.add(
            AsyncTask(
                id=TASK,
                project_id=project_id,
                initiated_by=1,
                service_type="audio",
                status="succeeded",
                idempotency_key="adopted-sample-fixture",
                request_hash="a" * 64,
                message_status="idle",
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
        )
        baseline.flush()
        baseline.add(
            AIGenerationRecord(
                id=RECORD,
                task_id=TASK,
                config_id=CONFIG,
                call_no=1,
                config_snapshot={"private": "private-model"},
                request_data={"private": "private-prompt"},
                response_data={"voice": {"voice_id": "provider-specific-private-id"}},
                status="succeeded",
                created_at=now,
                updated_at=now,
            )
        )
        baseline.add(
            MediaFile(
                id=SAMPLE,
                project_id=project_id,
                created_by=1,
                format_code="audio/wav",
                storage_locator="minio://fixture-audio/sample.wav",
                original_name="sample.wav",
                byte_size=128,
                duration_ms=3200,
                checksum_sha256="b" * 64,
                published_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.flush()
        baseline.add(
            CharacterVoice(
                project_id=project_id,
                asset_id=asset_id,
                row_version=1,
                record_id=RECORD,
                media_id=SAMPLE,
            )
        )


def test_member_reads_only_adopted_sample_without_private_record(catalog_session):
    session, baseline = catalog_session
    seed_voice(baseline)
    session.info["actor"] = actor(2)
    read = CanvasCharacterCatalogService(session, settings=SETTINGS).get(KEY, ASSET).item
    assert read.voice_status == "ready"
    assert read.voice_sample.media_id == SAMPLE and read.voice_sample.binding_version == 1
    serialized = read.model_dump(mode="json", by_alias=True)
    assert serialized["voice_sample"]["resource"]["durationMs"] == 3200
    assert not any(
        value in str(serialized)
        for value in [
            "private-model",
            "private-prompt",
            "record_id",
            "voice_id",
            "minio://",
            "storage_locator",
        ]
    )
    with session.begin():
        assert session.get(AIGenerationRecord, RECORD) is None
        assert session.get(AsyncTask, TASK) is None


def test_disabled_and_missing_voice_are_distinct(catalog_session):
    session, _ = catalog_session
    assert CanvasCharacterCatalogService(session).get(KEY, ASSET).item.voice_status == "unavailable"
    assert (
        CanvasCharacterCatalogService(session, settings=SETTINGS).get(KEY, ASSET).item.voice_status
        == "missing"
    )


@pytest.mark.parametrize("damage", ["unpublished", "scope", "duration", "size", "checksum", "kind"])
def test_invalid_or_unadopted_samples_are_rejected(catalog_session, damage):
    session, baseline = catalog_session
    seed_voice(baseline)
    with baseline.begin():
        media = baseline.get(MediaFile, SAMPLE)
        if damage == "unpublished":
            media.published_at = None
        elif damage == "scope":
            media.project_id = OWNING
        elif damage == "duration":
            media.duration_ms = 8000
        elif damage == "size":
            media.byte_size = 11 * 1024**2
        elif damage == "checksum":
            media.checksum_sha256 = None
        else:
            media.format_code = "video/mp4"
    session.info["actor"] = actor(2)
    with pytest.raises((NotFound, WorkflowError)):
        CanvasCharacterCatalogService(session, settings=SETTINGS).get(KEY, ASSET)


@pytest.mark.parametrize("scope", [OWNING, SOURCE])
def test_voice_keeps_both_project_permissions(catalog_session, scope):
    session, baseline = catalog_session
    seed_voice(baseline)
    with baseline.begin():
        member = baseline.scalar(select(ProjectMember).where(ProjectMember.project_id == scope))
        member.status = "removed"
    session.info["actor"] = actor(2)
    with pytest.raises(NotFound):
        CanvasCharacterCatalogService(session, settings=SETTINGS).get(KEY, ASSET)


def test_generation_freezes_binding_and_rejects_replacement_or_fake_key(catalog_session):
    session, baseline = catalog_session
    seed_voice(baseline)
    source = document(session, cover=None).model_dump(mode="json", by_alias=True)
    version = source["nodes"][0]["metadata"]["characterVersionId"]
    metadata = CanvasTaskMetadata.model_validate(
        {
            "nodeId": "target",
            "clientOperationId": "voice-sample-freeze",
            "resolvedCharacterVersions": [{"assetId": str(ASSET), "versionId": version}],
            "resolvedCharacterVoices": [
                {
                    "assetId": str(ASSET),
                    "versionId": version,
                    "bindingVersion": "1",
                    "sampleResourceId": str(SAMPLE),
                    "voiceKey": "",
                    "characterName": "client-name",
                    "instructions": "",
                }
            ],
        }
    )
    audios = [CanvasTaskReference(storage_key=f"resource:{SAMPLE}")]
    with session.begin():
        canvas = session.get(ProjectCanvas, CANVAS)
        result = freeze_canvas_characters(
            CanvasService(session),
            canvas,
            source,
            metadata,
            settings=SETTINGS,
            audio_references=audios,
        )
        voice = result[0]["voice_sample"]
        assert voice["media_id"] == str(SAMPLE) and voice["binding_version"] == "1"
        assert voice["checksum_sha256"] == "b" * 64
        assert "record_id" not in voice and result[0]["name"] != "client-name"
        forged = deepcopy(metadata)
        forged.resolved_character_voices[0].voice_key = "arbitrary-tts-id"
        with pytest.raises(WorkflowError) as rejected:
            freeze_canvas_characters(
                CanvasService(session),
                canvas,
                source,
                forged,
                settings=SETTINGS,
                audio_references=audios,
            )
        assert rejected.value.code == "canvas_character_voice_changed"
    with baseline.begin():
        baseline.get(CharacterVoice, (SOURCE, ASSET)).row_version = 2
    with session.begin(), pytest.raises(WorkflowError) as replaced:
        freeze_canvas_characters(
            CanvasService(session),
            session.get(ProjectCanvas, CANVAS),
            source,
            metadata,
            settings=SETTINGS,
            audio_references=audios,
        )
    assert replaced.value.code == "canvas_character_voice_changed"
