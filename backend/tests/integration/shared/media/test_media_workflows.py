import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration


def test_media_library_workspace_search_and_counts_preserve_account_isolation(db_session):
    from short_drama.core.exceptions import NotFound
    from short_drama.service.media.library.assets import MediaAssetService
    from tests.support.generation_fixtures import settings
    from tests.support.media_library_fixtures import seed_media_library

    db_session.info["actor"] = seed_media_library(db_session)
    service = MediaAssetService(db_session, settings, None)
    all_assets = service.list(0, 24)
    assert all_assets["total"] == 7
    assert [item["asset_id"] for item in all_assets["items"]] == [
        "4007",
        "4006",
        "4005",
        "4004",
        "4003",
        "4002",
        "4001",
    ]
    first = service.list(0, 1, {"workspace_mode": "infinite_canvas", "name": "雨夜"})
    second = service.list(1, 1, {"workspace_mode": "infinite_canvas", "name": "雨夜"})
    assert first["total"] == second["total"] == 3
    assert first["items"][0]["asset_id"] == "4006"
    assert second["items"][0]["asset_id"] == "4004"
    audio = service.list(0, 24, {"workspace_mode": "standard", "media_type": "audio"})
    assert audio["total"] == 1 and audio["items"][0]["asset_id"] == "4003"
    with pytest.raises(NotFound):
        service.detail(4008)


def media_context(session):
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.media.files.files import MediaFileService
    from short_drama.service.projects.management.project import ProjectService

    project = ProjectService(session).create_project({"name": "Media"})
    shot = ShotScriptService(session).create({"project_id": project.id, "position": 1})
    media = MediaFileService(session)
    first = media.create({"format_code": "image/png", "storage_locator": "test/image1.png"})
    second = media.create({"format_code": "image/png", "storage_locator": "test/image2.png"})
    video = media.create({"format_code": "video/mp4", "storage_locator": "test/video.mp4"})
    return project, shot, first, second, video


def test_replace_restore_and_discard_are_atomic(db_session):
    from short_drama.domain import MediaRecycleBin, ShotImage
    from short_drama.service.creation.storyboard.images.image import ShotImageService
    from short_drama.service.creation.storyboard.management.storyboard import (
        ProjectStoryboardService,
    )
    from short_drama.service.media.recycle.recycle import MediaRecycleBinService

    project, shot, first, second, _ = media_context(db_session)
    images = ShotImageService(db_session)
    board = ProjectStoryboardService(db_session)
    context_hash = board.get_shot_context(project.id, shot.id)["context_hash"]
    value = images.create(
        {
            "project_id": project.id,
            "shot_id": shot.id,
            "media_id": first.id,
            "aspect": "16:9",
            "prompt": "original",
            "context_hash": context_hash,
        }
    )
    replaced = images.update(value.id, {"media_id": second.id, "prompt": "replacement"})
    assert replaced.media_id == second.id
    assert replaced.context_hash == context_hash
    assert board.list(project.id)["items"][0]["image"]["is_stale"] is False
    recycle = MediaRecycleBinService(db_session)
    old = recycle.list().items[0]
    assert old.media_id == first.id
    assert old.prompt == "original"
    restored = recycle.restore(old.id)
    assert restored.media_id == first.id
    assert restored.prompt == "original"
    assert restored.context_hash is None
    restored_shot = board.list(project.id)["items"][0]
    assert restored_shot["image"]["media_id"] == str(first.id)
    # Confirmed media stays visible even when the recycled record has no context digest.
    assert restored_shot["image"]["is_stale"] is False
    assert [row.media_id for row in recycle.list().items] == [second.id]
    images.delete(restored.id)
    assert images.list().total == 0
    assert recycle.list().total == 2
    with db_session.begin():
        assert db_session.scalar(select(ShotImage)) is None
        assert len(list(db_session.scalars(select(MediaRecycleBin)))) == 2


def test_wrong_media_type_rolls_back_current_result(db_session):
    from short_drama.core.exceptions import BusinessError
    from short_drama.service.creation.storyboard.images.image import ShotImageService
    from short_drama.service.media.recycle.recycle import MediaRecycleBinService

    project, shot, first, _, video = media_context(db_session)
    images = ShotImageService(db_session)
    value = images.create(
        {"project_id": project.id, "shot_id": shot.id, "media_id": first.id, "aspect": "16:9"}
    )
    with pytest.raises(BusinessError):
        images.update(value.id, {"media_id": video.id})
    assert images.get(value.id).media_id == first.id
    assert MediaRecycleBinService(db_session).list().total == 0


def test_recycle_cannot_contain_current_result_or_be_edited(db_session):
    from short_drama.core.exceptions import BusinessError
    from short_drama.service.creation.storyboard.images.image import ShotImageService
    from short_drama.service.media.recycle.recycle import MediaRecycleBinService

    project, shot, first, _, _ = media_context(db_session)
    ShotImageService(db_session).create(
        {"project_id": project.id, "shot_id": shot.id, "media_id": first.id, "aspect": "16:9"}
    )
    recycle = MediaRecycleBinService(db_session)
    with pytest.raises(BusinessError):
        recycle.create(
            {
                "shot_id": shot.id,
                "media_id": first.id,
                "reason": "discarded",
                "resolution": "2K",
                "layout": "single",
                "aspect": "16:9",
            }
        )
    with pytest.raises(BusinessError):
        recycle.update(1, {"prompt": "tampered"})
    assert recycle.list().total == 0


def test_video_workflow_and_metadata_only_edits(db_session):
    from short_drama.service.creation.storyboard.videos.video import ShotVideoService
    from short_drama.service.media.recycle.recycle import MediaRecycleBinService

    project, shot, _, _, video = media_context(db_session)
    videos = ShotVideoService(db_session)
    result = videos.create(
        {"project_id": project.id, "shot_id": shot.id, "media_id": video.id, "duration": 3000}
    )
    same = videos.update(result.id, {"duration": 3000})
    assert same.updated_at == result.updated_at
    edited = videos.update(result.id, {"prompt": "corrected"})
    assert edited.prompt == "corrected"
    videos.delete(edited.id)
    recycle = MediaRecycleBinService(db_session)
    entry = recycle.list().items[0]
    restored = recycle.restore(entry.id)
    assert restored.duration == 3000
    assert restored.prompt == "corrected"
    assert recycle.list().total == 0
