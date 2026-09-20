from types import SimpleNamespace

import pytest
from generation_fixtures import config, generation_session, settings
from sqlalchemy import select


def test_four_candidates_do_not_adopt_until_explicit_apply_and_stale_target_conflicts():
    from short_drama.core.exceptions import Conflict
    from short_drama.domain import AIGenerationRecord, MediaAsset, MediaFile, ShotImage
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.base import utcnow
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.media_asset_service import MediaAssetService
    from short_drama.service.project_service import ProjectService
    from short_drama.service.shot_script_service import ShotScriptService

    with generation_session() as session:
        model = config(session)
        project = ProjectService(session).create({"name": "P", "aspect": "16:9"})
        episode = EpisodeService(session).create(
            {"project_id": project.id, "position": 1, "title": "E", "aspect": "16:9"}
        )
        shot = ShotScriptService(session).create({"episode_id": episode.id, "position": 1})
        generation, _ = AIGenerationService(session, settings).create(
            "image",
            {
                "input": {"prompt": "rain"},
                "parameters": {"count": 4, "aspect": "16:9", "resolution": "2K"},
                "source": {"scene": "shot_image", "shot_id": str(shot.id), "layout": "single"},
            },
            "candidates",
        )
        record = session.scalar(select(AIGenerationRecord))
        now = utcnow()
        for index in range(1, 5):
            session.add(
                MediaFile(
                    id=100 + index,
                    format_code="image/png",
                    storage_locator=f"minio://images/{index}.png",
                    original_name="",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                MediaAsset(
                    id=200 + index,
                    record_id=record.id,
                    output_index=index,
                    media_id=100 + index,
                    media_type="image",
                    name=f"Image{index}",
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        model.enabled = 0
        model.is_default = 0
        session.commit()
        assert AIGenerationService(session, settings).detail(generation["generation_id"])["result"][
            "assets"
        ]
        assert session.scalar(select(ShotImage)) is None
        session.rollback()
        assets = MediaAssetService(session, settings, None)
        body = {"target": {"type": "shot_image", "id": str(shot.id)}, "expected_media_id": None}
        assert assets.apply(201, body)["media_id"] == "101"
        assert assets.apply(201, body)["media_id"] == "101"
        with pytest.raises(Conflict):
            assets.apply(202, body)
        assert assets.apply(202, {**body, "expected_media_id": "101"})["media_id"] == "102"


def test_rename_requires_current_version():
    from short_drama.core.exceptions import Conflict
    from short_drama.domain import AIGenerationRecord, MediaAsset, MediaFile
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.base import utcnow
    from short_drama.service.media_asset_service import MediaAssetService

    with generation_session() as session:
        config(session)
        AIGenerationService(session, settings).create(
            "image", {"input": {"prompt": "rain"}}, "rename"
        )
        record = session.scalar(select(AIGenerationRecord))
        now = utcnow()
        session.add(
            MediaFile(
                id=101,
                format_code="image/png",
                storage_locator="minio://images/1.png",
                original_name="",
            )
        )
        session.add(
            MediaAsset(
                id=201,
                record_id=record.id,
                output_index=1,
                media_id=101,
                media_type="image",
                name="Old",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        storage = SimpleNamespace(presigned_get=lambda *_: "https://signed.example/1")
        configured = SimpleNamespace(
            minio_image_bucket="images", minio_video_bucket="videos", minio_presign_expiry=300
        )
        assets = MediaAssetService(session, configured, storage)
        assert assets.rename(201, {"name": "New", "row_version": "1"})["row_version"] == "2"
        with pytest.raises(Conflict):
            assets.rename(201, {"name": "Lost", "row_version": "1"})
        assert assets.detail(201)["name"] == "New"


@pytest.mark.parametrize(
    ("actual_ms", "explicit_ms", "expected_ms"),
    [(None, None, 6000), (6500, None, 6500), (6500, 7000, 7000)],
)
def test_video_apply_uses_milliseconds_and_business_parameters(actual_ms, explicit_ms, expected_ms):
    from short_drama.domain import AIGenerationRecord, MediaAsset, MediaFile, ShotVideo
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.base import utcnow
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.media_asset_service import MediaAssetService
    from short_drama.service.project_service import ProjectService
    from short_drama.service.shot_script_service import ShotScriptService

    with generation_session() as session:
        config(session, "video")
        project = ProjectService(session).create({"name": "P", "aspect": "16:9"})
        episode = EpisodeService(session).create(
            {"project_id": project.id, "position": 1, "title": "E", "aspect": "16:9"}
        )
        shot = ShotScriptService(session).create({"episode_id": episode.id, "position": 1})
        AIGenerationService(session, settings).create(
            "video",
            {
                "input": {"prompt": "rain"},
                "parameters": {"resolution": "720p", "duration_ms": 6000},
            },
            "duration",
        )
        record = session.scalar(select(AIGenerationRecord))
        # Provider fields carry different units/semantics than the business request.
        record.request_data = {
            **record.request_data,
            "resolved_parameters": {"duration": 6, "resolution": "provider-resolution"},
        }
        now = utcnow()
        session.add(
            MediaFile(
                id=101,
                format_code="video/mp4",
                storage_locator="minio://videos/1.mp4",
                original_name="",
                duration_ms=actual_ms,
            )
        )
        session.add(
            MediaAsset(
                id=201,
                record_id=record.id,
                output_index=1,
                media_id=101,
                media_type="video",
                name="Video",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        body = {"target": {"type": "shot_video", "id": str(shot.id)}, "expected_media_id": None}
        if explicit_ms is not None:
            body["parameters"] = {"duration": explicit_ms}
        MediaAssetService(session, settings, None).apply(201, body)
        confirmed = session.scalar(select(ShotVideo))
        assert confirmed.duration == expected_ms
        assert confirmed.resolution == "720p"
