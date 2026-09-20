import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration


def media_context(session):
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.media_file_service import MediaFileService
    from short_drama.service.project_service import ProjectService
    from short_drama.service.shot_script_service import ShotScriptService

    project = ProjectService(session).create({"name": "Media", "aspect": "16:9", "target_ms": 1000})
    episode = EpisodeService(session).create(
        {"project_id": project.id, "position": 1, "title": "One", "aspect": "16:9"}
    )
    shot = ShotScriptService(session).create({"episode_id": episode.id, "position": 1})
    media = MediaFileService(session)
    first = media.create({"format_code": "image/png", "storage_locator": "test/image1.png"})
    second = media.create({"format_code": "image/png", "storage_locator": "test/image2.png"})
    video = media.create({"format_code": "video/mp4", "storage_locator": "test/video.mp4"})
    return episode, shot, first, second, video


def test_replace_restore_and_discard_are_atomic(db_session):
    from short_drama.domain import MediaRecycleBin, ShotImage
    from short_drama.service.media_recycle_bin_service import MediaRecycleBinService
    from short_drama.service.shot_image_service import ShotImageService

    episode, shot, first, second, _ = media_context(db_session)
    images = ShotImageService(db_session)
    value = images.create(
        {
            "episode_id": episode.id,
            "shot_id": shot.id,
            "media_id": first.id,
            "aspect": "16:9",
            "prompt": "original",
        }
    )
    replaced = images.update(value.id, {"media_id": second.id, "prompt": "replacement"})
    assert replaced.media_id == second.id
    recycle = MediaRecycleBinService(db_session)
    old = recycle.list().items[0]
    assert old.media_id == first.id
    assert old.prompt == "original"
    restored = recycle.restore(old.id)
    assert restored.media_id == first.id
    assert restored.prompt == "original"
    assert [row.media_id for row in recycle.list().items] == [second.id]
    images.delete(restored.id)
    assert images.list().total == 0
    assert recycle.list().total == 2
    with db_session.begin():
        assert db_session.scalar(select(ShotImage)) is None
        assert len(list(db_session.scalars(select(MediaRecycleBin)))) == 2


def test_wrong_media_type_rolls_back_current_result(db_session):
    from short_drama.core.exceptions import BusinessError
    from short_drama.service.media_recycle_bin_service import MediaRecycleBinService
    from short_drama.service.shot_image_service import ShotImageService

    episode, shot, first, _, video = media_context(db_session)
    images = ShotImageService(db_session)
    value = images.create(
        {"episode_id": episode.id, "shot_id": shot.id, "media_id": first.id, "aspect": "16:9"}
    )
    with pytest.raises(BusinessError):
        images.update(value.id, {"media_id": video.id})
    assert images.get(value.id).media_id == first.id
    assert MediaRecycleBinService(db_session).list().total == 0


def test_recycle_cannot_contain_current_result_or_be_edited(db_session):
    from short_drama.core.exceptions import BusinessError
    from short_drama.service.media_recycle_bin_service import MediaRecycleBinService
    from short_drama.service.shot_image_service import ShotImageService

    episode, shot, first, _, _ = media_context(db_session)
    ShotImageService(db_session).create(
        {"episode_id": episode.id, "shot_id": shot.id, "media_id": first.id, "aspect": "16:9"}
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
    from short_drama.service.media_recycle_bin_service import MediaRecycleBinService
    from short_drama.service.shot_video_service import ShotVideoService

    episode, shot, _, _, video = media_context(db_session)
    videos = ShotVideoService(db_session)
    result = videos.create(
        {"episode_id": episode.id, "shot_id": shot.id, "media_id": video.id, "duration": 3000}
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
