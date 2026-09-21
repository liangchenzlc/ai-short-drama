"""MySQL service transactions and JSON source filtering in an isolated database."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain import AIGenerationRecord, MediaAsset, MediaFile, ShotImage
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.media_asset_service import MediaAssetService
from short_drama.service.project_service import ProjectService
from short_drama.service.shot_script_service import ShotScriptService

pytestmark = pytest.mark.integration


def test_generation_source_history_candidates_and_explicit_adoption(db_session):
    settings = SimpleNamespace(generation_archive_budget_seconds=86400)
    model = AIModelConfigService(db_session).create(
        {
            "service_type": "image",
            "name": "Ark fixture",
            "model_key": "doubao-seedream",
            "provider": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        }
    )
    project = ProjectService(db_session).create({"name": "Source", "aspect": "16:9"})
    episode = EpisodeService(db_session).create(
        {"project_id": project.id, "position": 1, "title": "E", "aspect": "16:9"}
    )
    shot = ShotScriptService(db_session).create(
        {"episode_id": episode.id, "position": 1, "script": "Original script"}
    )
    service = AIGenerationService(db_session, settings)
    request = {
        "config_id": str(model.id),
        "input": {"prompt": "Rain"},
        "parameters": {"aspect": "16:9", "resolution": "2K", "count": 4},
        "source": {"scene": "shot_image", "shot_id": str(shot.id), "layout": "single"},
    }
    created, is_new = service.create("image", request, "mysql-generation")
    assert is_new
    assert service.create("image", request, "mysql-generation")[1] is False
    with pytest.raises(Conflict):
        service.create("image", {**request, "input": {"prompt": "Sun"}}, "mysql-generation")
    filters = {"source_scene": "shot_image", "source_id": shot.id}
    assert service.list(filters=filters)["total"] == 1
    assert service.list(filters={**filters, "source_id": shot.id + 1})["total"] == 0
    with db_session.begin():
        record = db_session.scalar(select(AIGenerationRecord))
        assert record.request_data["source_snapshot"]["script"] == "Original script"
        record_id = record.id
        now = utcnow()
        for index in range(1, 5):
            db_session.add(
                MediaFile(
                    id=100 + index,
                    format_code="image/png",
                    storage_locator=f"minio://images/{index}.png",
                    original_name="",
                    created_at=now,
                    updated_at=now,
                )
            )
        db_session.flush()
        for index in range(1, 5):
            db_session.add(
                MediaAsset(
                    id=200 + index,
                    record_id=record_id,
                    output_index=index,
                    media_id=100 + index,
                    media_type="image",
                    name=f"Image{index}",
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
    assert len(service.detail(created["generation_id"])["result"]["assets"]) == 4
    with db_session.begin():
        assert db_session.scalar(select(ShotImage)) is None
    assets = MediaAssetService(db_session, settings, None)
    assert assets.list(filters=filters)["total"] == 4
    context = EpisodeStoryboardService(db_session).get_shot_context(project.id, episode.id, shot.id)
    target = {
        "target": {"type": "shot_image", "id": str(shot.id)},
        "expected_media_id": None,
        "expected_row_version": str(shot.row_version),
        "expected_context_hash": context["context_hash"],
        "acknowledge_stale_source": True,
    }
    applied = assets.apply(201, target)
    assert applied["media_id"] == "101"
    assert assets.apply(201, target)["media_id"] == "101"
    with pytest.raises(WorkflowError, match="分镜内容已变化"):
        assets.apply(202, target)
    assert (
        assets.apply(
            202,
            {**target, "expected_media_id": "101", "expected_row_version": applied["row_version"]},
        )["media_id"]
        == "102"
    )
