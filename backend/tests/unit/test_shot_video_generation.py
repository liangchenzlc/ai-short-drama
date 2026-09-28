from types import SimpleNamespace

import pytest
from generation_fixtures import config, generation_session, settings
from sqlalchemy import select
from test_episode_storyboard import setup_storyboard

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AIGenerationRecord, MediaAsset, MediaFile, ShotImage, ShotVideo
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.media_asset_service import MediaAssetService
from short_drama.service.shot_image_service import ShotImageService


def setup_video(session):
    model = config(session, "video")
    model.base_url = "https://api.modelhub.cc"
    model.model_key = "seedance-2.0-mini"
    session.commit()
    project, episode = setup_storyboard(session)
    storyboard = EpisodeStoryboardService(session)
    shot = storyboard.create(
        project,
        episode,
        {
            "storyboard_version": "1",
            "script": "林晚先抬眼，再用右手拿起红伞。镜头缓推至中近景。",
            "duration_ms": 5000,
        },
        "video-shot",
    )["shot"]
    session.add(
        MediaFile(
            id=101,
            format_code="image/png",
            storage_locator="minio://images/frame.png",
            original_name="",
        )
    )
    session.commit()
    ShotImageService(session).create(
        {
            "episode_id": episode,
            "shot_id": shot["id"],
            "media_id": "101",
            "layout": "single",
            "aspect": "16:9",
            "resolution": "2K",
            "context_hash": shot["context_hash"],
        }
    )
    return SimpleNamespace(
        session=session,
        project=project,
        episode=episode,
        shot_id=shot["id"],
        storyboard=storyboard,
        generations=AIGenerationService(session, settings),
    )


def current(flow):
    return flow.storyboard.get(flow.project, flow.episode, flow.shot_id)["shot"]


def request(shot):
    return {
        "source": {
            "scene": "shot_video",
            "shot_id": shot["id"],
            "row_version": shot["row_version"],
            "context_hash": shot["video_context_hash"],
            "reference_media_id": shot["image"]["media_id"],
        }
    }


def output(flow, task_id, media_id=201):
    session = flow.session
    record = session.scalar(
        select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
    )
    session.add(
        MediaFile(
            id=media_id,
            format_code="video/mp4",
            storage_locator=f"minio://videos/{media_id}.mp4",
            original_name="",
            duration_ms=5000,
        )
    )
    session.add(
        MediaAsset(
            id=media_id + 100,
            record_id=record.id,
            output_index=1,
            media_id=media_id,
            media_type="video",
            name="候选视频",
            row_version=1,
        )
    )
    session.commit()
    return media_id + 100


def adoption(shot):
    return {
        "target": {"type": "shot_video", "id": shot["id"]},
        "expected_media_id": shot["video"]["media_id"] if shot["video"] else None,
        "expected_row_version": shot["row_version"],
        "expected_context_hash": shot["video_context_hash"],
    }


@pytest.mark.parametrize("layout", ["single", "four", "five", "nine"])
def test_video_saved_snapshot_replay_and_history(layout):
    with generation_session() as session:
        flow = setup_video(session)
        session.scalar(select(ShotImage)).layout = layout
        session.commit()
        shot = current(flow)
        body = request(shot)
        task, created = flow.generations.create("video", body, "video-generation")
        assert created
        replay, created = flow.generations.create("video", body, "video-generation")
        assert not created and replay["generation_id"] == task["generation_id"]
        detail = flow.generations.detail(task["generation_id"])
        assert detail["input"]["reference_media_ids"] == ["101"]
        assert "first_frame_media_id" not in detail["input"]
        assert detail["source_snapshot"]["reference_layout"] == layout
        assert detail["source_snapshot"]["input_mode"] == "omni_reference"
        assert detail["parameters"] == {"duration_ms": 5000, "resolution": "720p", "aspect": "16:9"}
        assert "林晚先抬眼" in detail["source_snapshot"]["user_prompt"]
        assert "光源方向" in detail["source_snapshot"]["system_prompt"]
        assert "minio://" not in detail["effective_prompt"]
        assert current(flow)["video"] is None
        asset_id = output(flow, task["generation_id"])
        filters = {"source_scene": "shot_video", "source_id": shot["id"]}
        assert flow.generations.list(filters=filters)["total"] == 1
        media = MediaAssetService(session, settings, None)
        assert media.list(filters=filters)["total"] == 1
        apply_body = adoption(current(flow))
        applied = media.apply(asset_id, apply_body)
        assert media.apply(asset_id, apply_body) == applied
        adopted = current(flow)
        assert adopted["video"]["media_id"] == "201"
        assert not adopted["video"]["is_stale"]
        assert not adopted["image"]["is_stale"]
        updated = flow.storyboard.update(
            flow.project,
            flow.episode,
            shot["id"],
            {
                "row_version": adopted["row_version"],
                "video_prompt": "右手握住伞柄，固定机位。",
            },
        )["shot"]
        assert updated["video"]["is_stale"] and not updated["image"]["is_stale"]
        assert (
            flow.generations.detail(task["generation_id"])["source_snapshot"]
            == detail["source_snapshot"]
        )


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("missing", "video_reference_required"),
        ("stale", "video_reference_stale"),
        ("frame", "shot_version_conflict"),
        ("version", "shot_version_conflict"),
        ("settings", "generation_settings_changed"),
    ],
)
def test_video_rejects_invalid_context_before_task_creation(change, code):
    with generation_session() as session:
        flow = setup_video(session)
        body = request(current(flow))
        image = session.scalar(select(ShotImage))
        if change == "missing":
            session.delete(image)
        if change == "stale":
            image.context_hash = "a" * 64
        if change == "frame":
            body["source"]["reference_media_id"] = "999"
        if change == "version":
            body["source"]["row_version"] = "999"
        if change == "settings":
            body["parameters"] = {"duration_ms": 6000}
        session.commit()
        with pytest.raises(WorkflowError) as caught:
            flow.generations.create("video", body, "bad-context")
        assert caught.value.code == code
        assert flow.generations.list(filters={"service_type": "video"})["total"] == 0


def test_video_duration_override_is_saved_without_invalidating_reference_image():
    with generation_session() as session:
        flow = setup_video(session)
        shot = current(flow)
        task, _ = flow.generations.create("video", request(shot), "original-duration")
        asset_id = output(flow, task["generation_id"])
        MediaAssetService(session, settings, None).apply(asset_id, adoption(current(flow)))
        adopted = current(flow)
        updated = flow.storyboard.update(
            flow.project,
            flow.episode,
            flow.shot_id,
            {
                "row_version": adopted["row_version"],
                "video_settings": {"resolution": "720p", "duration_ms": 15000},
            },
        )["shot"]
        assert updated["duration_ms"] == 5000
        assert updated["context_hash"] == shot["context_hash"]
        assert not updated["image"]["is_stale"] and updated["video"]["is_stale"]
        assert current(flow)["video_settings"]["duration_ms"] == 15000
        body = request(updated)
        body["parameters"] = {"resolution": "720p", "duration_ms": 15000}
        task, _ = flow.generations.create("video", body, "longer-video")
        detail = flow.generations.detail(task["generation_id"])
        assert detail["parameters"]["duration_ms"] == 15000
        assert detail["source_snapshot"]["video_duration_ms"] == 15000
        assert detail["source_snapshot"]["shot"]["duration_ms"] == 5000
        assert "本次视频时长：15 秒" in detail["effective_prompt"]
        body["parameters"]["duration_ms"] = 5000
        with pytest.raises(WorkflowError) as caught:
            flow.generations.create("video", body, "unsaved-duration")
        assert caught.value.code == "generation_settings_changed"


def test_video_adoption_blocks_concurrent_edits_even_when_stale_is_acknowledged():
    with generation_session() as session:
        flow = setup_video(session)
        shot = current(flow)
        task, _ = flow.generations.create("video", request(shot), "adoption")
        asset_id = output(flow, task["generation_id"])
        old = adoption(shot)
        flow.storyboard.update(
            flow.project,
            flow.episode,
            shot["id"],
            {"row_version": shot["row_version"], "video_prompt": "缓慢抬头"},
        )
        media = MediaAssetService(session, settings, None)
        with pytest.raises(WorkflowError, match="分镜"):
            media.apply(asset_id, {**old, "acknowledge_stale_source": True})
        with pytest.raises(WorkflowError) as caught:
            media.apply(asset_id, adoption(current(flow)))
        assert caught.value.code == "stale_generation_source"
        media.apply(asset_id, {**adoption(current(flow)), "acknowledge_stale_source": True})
        assert current(flow)["video"]["is_stale"]
        flow.storyboard.archive(
            flow.project, flow.episode, shot["id"], current(flow)["row_version"]
        )
        with pytest.raises(WorkflowError) as caught:
            media.apply(asset_id, {**adoption(current(flow)), "acknowledge_stale_source": True})
        assert caught.value.code == "shot_archived"
        assert session.scalar(select(ShotVideo)).media_id == 201
