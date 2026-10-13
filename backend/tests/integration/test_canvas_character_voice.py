"""独占 MySQL 角色已采用样音读取／缓存竞争；不调用对象或供应商。"""

import os

import pytest

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import AIGenerationRecord, AsyncTask, ProjectCanvas
from short_drama.domain.native_voice import CharacterVoice
from short_drama.schemas.canvas_task_runtime import CanvasTaskMetadata, CanvasTaskReference
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService
from short_drama.service.canvas_character_generation import freeze_canvas_characters
from short_drama.service.canvas_service import CanvasService
from tests.integration.test_canvas_character_rr import account_session, document, system_session
from tests.integration.test_canvas_character_rr import rr_case as rr_case
from tests.unit.test_canvas_character_voice import RECORD, SAMPLE, TASK, seed_voice

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_CHARACTER_RR") != "1", reason="显式启用角色真实 MySQL 回归"
    ),
]
SETTINGS = Settings(
    _env_file=None,
    native_video_enabled=True,
    audio_production_enabled=True,
    minio_image_bucket="canvas-character-rr-test",
    minio_audio_bucket="fixture-audio",
)


def test_mysql_member_sample_freeze_and_cached_binding_replacement(rr_case):
    case = rr_case
    with system_session(case) as writer:
        seed_voice(writer, project_id=case.source, asset_id=case.asset)
    source = document(case).model_dump(mode="json", by_alias=True)
    version = source["nodes"][0]["metadata"]["characterVersionId"]
    metadata = CanvasTaskMetadata.model_validate(
        {
            "nodeId": "target",
            "clientOperationId": "mysql-voice-freeze",
            "resolvedCharacterVersions": [{"assetId": str(case.asset), "versionId": version}],
            "resolvedCharacterVoices": [
                {
                    "assetId": str(case.asset),
                    "versionId": version,
                    "bindingVersion": "1",
                    "sampleResourceId": str(SAMPLE),
                    "voiceKey": "",
                }
            ],
        }
    )
    with account_session(case) as reader:
        first = CanvasCharacterCatalogService(reader, settings=SETTINGS).get(case.key, case.asset)
        assert first.item.voice_status == "ready" and first.item.voice_sample.media_id == SAMPLE
        with reader.begin():
            held = reader.get(CharacterVoice, (case.source, case.asset))
            assert held.row_version == 1
            assert (
                reader.get(AIGenerationRecord, RECORD) is None
                and reader.get(AsyncTask, TASK) is None
            )
            frozen = freeze_canvas_characters(
                CanvasService(reader),
                reader.get(ProjectCanvas, case.canvas),
                source,
                metadata,
                settings=SETTINGS,
                audio_references=[CanvasTaskReference(storage_key=f"resource:{SAMPLE}")],
            )
            assert frozen[0]["voice_sample"]["checksum_sha256"] == "b" * 64
        with system_session(case) as writer, writer.begin():
            writer.get(
                CharacterVoice, (case.source, case.asset), with_for_update=True
            ).row_version = 2
        next_read = CanvasCharacterCatalogService(reader, settings=SETTINGS).get(
            case.key, case.asset
        )
        assert next_read.item.voice_sample.binding_version == held.row_version == 2
        with reader.begin(), pytest.raises(WorkflowError) as stale:
            freeze_canvas_characters(
                CanvasService(reader),
                reader.get(ProjectCanvas, case.canvas),
                source,
                metadata,
                settings=SETTINGS,
                audio_references=[CanvasTaskReference(storage_key=f"resource:{SAMPLE}")],
            )
        assert stale.value.code == "canvas_character_voice_changed"


def test_mysql_unpublished_voice_is_not_an_adopted_shared_sample(rr_case):
    from short_drama.domain import MediaFile

    case = rr_case
    with system_session(case) as writer:
        seed_voice(writer, project_id=case.source, asset_id=case.asset)
    with system_session(case) as writer, writer.begin():
        writer.get(MediaFile, SAMPLE, with_for_update=True).published_at = None
    with account_session(case) as reader, pytest.raises(NotFound):
        CanvasCharacterCatalogService(reader, settings=SETTINGS).get(case.key, case.asset)
