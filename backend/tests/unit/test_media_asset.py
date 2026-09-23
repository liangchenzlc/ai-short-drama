from types import SimpleNamespace

import pytest
from generation_fixtures import config, generation_session, settings
from sqlalchemy import select


@pytest.mark.parametrize("provider", ["ark", "openai"])
def test_four_candidates_do_not_adopt_until_explicit_apply_and_stale_target_conflicts(provider):
    from short_drama.core.exceptions import WorkflowError
    from short_drama.domain import AIGenerationRecord, MediaAsset, MediaFile, ShotImage
    from short_drama.service.ai_generation_service import AIGenerationService
    from short_drama.service.base import utcnow
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
    from short_drama.service.media_asset_service import MediaAssetService
    from short_drama.service.project_service import ProjectService
    from short_drama.service.shot_script_service import ShotScriptService

    with generation_session() as session:
        model = config(session)
        if provider == "openai":
            model.base_url = "https://relay.example/v1"
            model.model_key = "gpt-image-2.5-flare"
            session.commit()
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
        detail = AIGenerationService(session, settings).detail(generation["generation_id"])
        assert detail["result"]["assets"]
        assert detail["source"]["shot_id"] == str(shot.id)
        assert detail["source"]["layout"] == "single"
        assert detail["parameters"] == {"count": 4, "aspect": "16:9", "resolution": "2K"}
        assert detail["resolved_parameters"] != detail["parameters"]
        assert session.scalar(select(ShotImage)) is None
        session.rollback()
        assets = MediaAssetService(session, settings, None)
        board = EpisodeStoryboardService(session)
        current = board.update(
            project.id,
            episode.id,
            shot.id,
            {
                "row_version": str(shot.row_version),
                "image_settings": {"layout": "single", "aspect": "inherit", "resolution": "4K"},
            },
        )["shot"]
        body = {
            "target": {"type": "shot_image", "id": str(shot.id)},
            "expected_media_id": None,
            "expected_row_version": current["row_version"],
            "expected_context_hash": current["context_hash"],
            "acknowledge_stale_source": True,
        }
        with pytest.raises(WorkflowError) as stale:
            assets.apply(201, {**body, "acknowledge_stale_source": False})
        assert stale.value.code == "stale_generation_source"
        for stale_token in (
            {"expected_row_version": str(int(current["row_version"]) - 1)},
            {"expected_context_hash": "0" * 64},
            {"expected_media_id": "104"},
        ):
            with pytest.raises(WorkflowError) as conflict:
                assets.apply(201, {**body, **stale_token})
            assert conflict.value.code == "shot_version_conflict"
        with pytest.raises(WorkflowError) as parameters:
            assets.apply(201, {**body, "parameters": {"resolution": "4K"}})
        assert parameters.value.code == "generation_parameters_conflict"
        applied = assets.apply(201, body)
        assert applied["media_id"] == "101"
        assert assets.apply(201, body) == applied
        saved = board.get(project.id, episode.id, shot.id)["shot"]
        assert saved["image"]["resolution"] == "2K"
        assert saved["image"]["layout"] == "single"
        assert saved["image"]["aspect"] == "16:9"
        assert saved["image_settings"]["resolution"] == "4K"
        with pytest.raises(WorkflowError, match="分镜内容已变化"):
            assets.apply(202, body)
        assert (
            assets.apply(
                202,
                {
                    **body,
                    "expected_media_id": "101",
                    "expected_row_version": applied["row_version"],
                },
            )["media_id"]
            == "102"
        )
        from short_drama.domain import MediaRecycleBin

        with session.begin():
            assert len(list(session.scalars(select(MediaRecycleBin)))) == 1
            assert session.get(MediaFile, 101) is not None

        replacement_body = {
            **body,
            "expected_media_id": "101",
            "expected_row_version": applied["row_version"],
        }
        repeated = assets.apply(202, replacement_body)
        saved = board.get(project.id, episode.id, shot.id)["shot"]
        assert saved["row_version"] == repeated["row_version"]
        assert saved["image"]["media_id"] == "102"
        assert saved["image"]["resolution"] == "2K"
        assert saved["image_settings"]["resolution"] == "4K"
        with session.begin():
            assert len(list(session.scalars(select(MediaRecycleBin)))) == 1


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
