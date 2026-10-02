import subprocess

import pytest
from generation_fixtures import generation_session
from sqlalchemy import select
from test_shot_video_generation import current, setup_video

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Episode, EpisodeAssembly, EpisodeRenderJob, MediaFile, ShotVideo
from short_drama.domain.episode_sound import EpisodeSound
from short_drama.schemas.episode_sound import SoundDocument
from short_drama.service.base import utcnow
from short_drama.service.episode_assembly_service import EpisodeAssemblyService, digest
from short_drama.service.episode_sound_service import EpisodeSoundService
from short_drama.service.video_render import RenderCancelled, VideoRenderer, executable


def flow(session, ready=True):
    f = setup_video(session)
    shot = current(f)
    session.add(
        MediaFile(
            id=201,
            format_code="video/mp4",
            storage_locator="minio://video/source.mp4",
            original_name="test.mp4",
            video_metadata={"duration_ms": 4200, "width": 640, "height": 360, "has_audio": False}
            if ready
            else None,
        )
    )
    session.flush()
    session.add(
        ShotVideo(
            id=301,
            episode_id=int(f.episode),
            shot_id=int(f.shot_id),
            media_id=201,
            resolution="720p",
            duration=5000,
            context_hash=shot["video_context_hash"],
        )
    )
    session.commit()
    return f, EpisodeAssemblyService(session)


def body(state):
    return {
        "row_version": state["assembly"]["row_version"],
        "resolution": state["assembly"]["resolution"],
        "clips": [
            {k: c[k] for k in ("id", "included", "muted", "trim_in_ms", "trim_out_ms")}
            for c in state["clips"]
        ],
    }


def export_body(state):
    return {"row_version": state["assembly"]["row_version"], "source_hash": state["source_hash"]}


def test_assembly_actual_duration_snapshot_replay_conflict_and_retry():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        assert state["clips"][0]["duration_ms"] == 4200
        assert not state["clips"][0]["is_stale"]
        payload = body(state)
        payload["clips"][0].update(trim_in_ms=500, trim_out_ms=3500, muted=True)
        changed = svc.edit(f.project, f.episode, payload)
        with pytest.raises(WorkflowError, match="其他窗口"):
            svc.edit(f.project, f.episode, payload)
        job = svc.export(f.project, f.episode, export_body(changed), "export-key")
        assert (
            svc.export(f.project, f.episode, export_body(changed), "export-key")["id"] == job["id"]
        )
        row = session.get(EpisodeRenderJob, int(job["id"]))
        assert row.snapshot["clips"][0]["trim_in_ms"] == 500
        assert row.snapshot["clips"][0]["muted"] is True
        session.commit()
        payload = body(changed)
        payload["clips"][0]["trim_in_ms"] = 1000
        latest = svc.edit(f.project, f.episode, payload)
        assert latest["context_hash"] != job["context_hash"]
        cancelled = svc.job_action(f.project, f.episode, job["id"], "cancel")
        assert cancelled["status"] == "cancelled"
        retried = svc.job_action(f.project, f.episode, job["id"], "retry", key="retry-key")
        assert retried["context_hash"] == job["context_hash"]
        assert (
            svc.job_action(f.project, f.episode, job["id"], "retry", key="retry-key")["id"]
            == retried["id"]
        )


def test_missing_video_blocks_export_and_sync_preserves_replaced_trim():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        edit = body(state)
        edit["clips"][0]["trim_in_ms"] = 500
        state = svc.edit(f.project, f.episode, edit)
        session.add(
            MediaFile(
                id=202,
                format_code="video/mp4",
                storage_locator="minio://video/new.mp4",
                original_name="",
            )
        )
        session.flush()
        session.scalar(select(ShotVideo)).media_id = 202
        session.commit()
        changed = svc.get(f.project, f.episode)
        assert changed["clips"][0]["media_id"] == "201"
        assert changed["changes"][0]["kind"] == "replacement"
        with pytest.raises(WorkflowError):
            svc.export(f.project, f.episode, export_body(state), "old")
        with pytest.raises(WorkflowError):
            svc.export(f.project, f.episode, export_body(changed), "unacknowledged")
        synced = svc.sync(f.project, f.episode, export_body(changed))
        assert synced["clips"][0]["trim_in_ms"] == 500
        assert synced["clips"][0]["issue"] == "preparing"
        with pytest.raises(WorkflowError):
            svc.export(f.project, f.episode, export_body(synced), "preparing")
        assert synced["jobs"][0]["kind"] == "probe"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_sync_during_probe_schedules_replacement_without_duplicate_work(status):
    with generation_session() as session:
        f, svc = flow(session)
        session.get(MediaFile, 201).video_metadata = {}
        session.commit()
        state = svc.initialize(f.project, f.episode)
        original = session.get(EpisodeRenderJob, int(state["jobs"][0]["id"]))
        original.status = status
        session.add(
            MediaFile(
                id=202,
                format_code="video/mp4",
                storage_locator="minio://video/replacement.mp4",
                original_name="replacement.mp4",
            )
        )
        session.flush()
        session.scalar(select(ShotVideo)).media_id = 202
        session.commit()

        latest = svc.get(f.project, f.episode)
        synced = svc.sync(f.project, f.episode, export_body(latest))
        assert synced["clips"][0]["media_id"] == "202"
        assert synced["clips"][0]["issue"] == "preparing"
        for _ in range(2):
            svc.initialize(f.project, f.episode)
        jobs = list(session.scalars(select(EpisodeRenderJob)))
        assert len(jobs) == 2
        assert sorted(j.snapshot["media"][0]["id"] for j in jobs) == ["201", "202"]


def test_render_job_timeline_keeps_original_media_after_replacement():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        job = svc.export(f.project, f.episode, export_body(state), "frozen-source")
        svc._url = lambda media: f"source:{media.id}" if media else None
        svc._derived_url = lambda metadata, key: (metadata or {}).get(key)
        original = session.get(MediaFile, 201)
        original.video_metadata = {
            **original.video_metadata,
            "preview_locator": "original-preview",
            "thumbnail_locator": "original-thumbnail",
            "filmstrip_locator": "original-filmstrip",
            "filmstrip_count": 4,
            "filmstrip_interval_ms": 1050,
        }
        session.add(
            MediaFile(
                id=202,
                format_code="video/mp4",
                storage_locator="minio://video/replacement.mp4",
                original_name="replacement.mp4",
                video_metadata={"duration_ms": 1000, "width": 640, "height": 360},
            )
        )
        session.flush()
        session.scalar(select(ShotVideo)).media_id = 202
        session.get(Episode, int(f.episode)).aspect = "9:16"
        session.commit()
        latest = svc.get(f.project, f.episode)
        svc.sync(f.project, f.episode, export_body(latest))
        result = svc.job_action(f.project, f.episode, job["id"], "get")
        assert result["timeline"][0]["media_id"] == "201"
        assert result["timeline"][0]["duration_ms"] == 4200
        assert result["timeline"][0]["trim_out_ms"] == 4200
        assert result["timeline"][0]["url"] == "original-preview"
        assert result["timeline"][0]["poster"] == "original-thumbnail"
        assert result["timeline"][0]["filmstrip"]["url"] == "original-filmstrip"
        assert result["aspect"] == "16:9" and result["resolution"] == "720p"


def test_trim_bounds_scope_and_empty_export():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        edit = body(state)
        edit["clips"][0]["trim_out_ms"] = 5000
        with pytest.raises(WorkflowError, match="实际时长"):
            svc.edit(f.project, f.episode, edit)
        with pytest.raises(WorkflowError):
            svc.get(999, f.episode)
        edit = body(state)
        edit["clips"][0]["included"] = False
        state = svc.edit(f.project, f.episode, edit)
        with pytest.raises(WorkflowError):
            svc.export(f.project, f.episode, export_body(state), "empty")


def test_initialize_keeps_reserved_sources_without_blocking_ready_video():
    with generation_session() as session:
        f, svc = flow(session)
        missing = f.storyboard.create(
            f.project,
            f.episode,
            {
                "storyboard_version": f.storyboard.get(f.project, f.episode, f.shot_id)[
                    "storyboard_version"
                ],
                "script": "No video adopted",
                "duration_ms": 5000,
            },
            "missing-source",
        )["shot"]
        assert svc.get(f.project, f.episode)["source_count"] == 1
        state = svc.initialize(f.project, f.episode)
        absent = next(c for c in state["clips"] if c["shot_id"] == missing["id"])
        assert absent["issue"] == "missing" and absent["included"] is True
        assert absent["is_stale"] is True
        assert len(state["sources"]) == 2
        job = svc.export(f.project, f.episode, export_body(state), "ready-only")
        assert [c["media_id"] for c in job["timeline"]] == ["201"]

        # Explicit exclusion is retained without affecting the effective render timeline.
        edited = body(state)
        next(c for c in edited["clips"] if c["id"] == absent["id"])["included"] = False
        state = svc.edit(f.project, f.episode, edited)
        state = svc.sync(f.project, f.episode, export_body(state))
        assert next(c for c in state["clips"] if c["id"] == absent["id"])["included"] is False
        assert state["context_hash"] == job["context_hash"]


def test_sync_reserves_empty_source_and_includes_it_after_video_adoption():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        original_hash = state["context_hash"]
        added = f.storyboard.create(
            f.project,
            f.episode,
            {
                "storyboard_version": f.storyboard.get(f.project, f.episode, f.shot_id)[
                    "storyboard_version"
                ],
                "script": "New source",
                "duration_ms": 5000,
            },
            "later-source",
        )["shot"]
        latest = svc.get(f.project, f.episode)
        state = svc.sync(f.project, f.episode, export_body(latest))
        reserved = next(c for c in state["clips"] if c["shot_id"] == added["id"])
        assert reserved["included"] is True and reserved["media_id"] is None
        assert state["context_hash"] == original_hash
        job = svc.export(f.project, f.episode, export_body(state), "reserved-source-export")
        assert [c["media_id"] for c in job["timeline"]] == ["201"]
        session.add(
            ShotVideo(
                id=302,
                episode_id=int(f.episode),
                shot_id=int(added["id"]),
                media_id=201,
                resolution="720p",
                duration=5000,
                context_hash=added["video_context_hash"],
            )
        )
        session.commit()
        latest = svc.get(f.project, f.episode)
        state = svc.sync(f.project, f.episode, export_body(latest))
        adopted = next(c for c in state["clips"] if c["id"] == reserved["id"])
        assert adopted["media_id"] == "201" and adopted["included"] is True
        assert state["context_hash"] != original_hash
        job = svc.export(f.project, f.episode, export_body(state), "adopted-source-export")
        assert len(job["timeline"]) == 2


def test_source_count_requires_an_adopted_video():
    with generation_session() as session:
        f, svc = flow(session)
        session.delete(session.scalar(select(ShotVideo)))
        session.commit()
        assert svc.get(f.project, f.episode)["source_count"] == 0
        state = svc.initialize(f.project, f.episode)
        assert state["clips"][0]["included"] is True
        with pytest.raises(WorkflowError) as error:
            svc.export(f.project, f.episode, export_body(state), "all-reserved")
        assert error.value.code == "assembly_not_ready"


@pytest.mark.parametrize("issue", ["preparing", "invalid", "trim"])
def test_existing_unusable_video_still_blocks_export(issue):
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        if issue == "trim":
            edit = body(state)
            edit["clips"][0]["trim_in_ms"] = 2000
            state = svc.edit(f.project, f.episode, edit)
        session.get(MediaFile, 201).video_metadata = {
            "preparing": {},
            "invalid": {"error": "unreadable"},
            "trim": {"duration_ms": 1000, "has_audio": False},
        }[issue]
        session.commit()
        state = svc.get(f.project, f.episode)
        assert state["clips"][0]["media_id"] == "201"
        assert state["clips"][0]["issue"] == issue
        with pytest.raises(WorkflowError) as error:
            svc.export(f.project, f.episode, export_body(state), f"unusable-{issue}")
        assert error.value.code == "assembly_not_ready"


def test_reserved_shot_does_not_change_reviewed_sound_timeline():
    with generation_session() as session:
        f, svc = flow(session)
        svc.settings = Settings(_env_file=None, audio_production_enabled=True)
        state = svc.initialize(f.project, f.episode)
        assembly = session.get(EpisodeAssembly, int(state["assembly"]["id"]))
        reviewed_hash = digest(svc._video_snapshot(assembly, svc._clips(assembly)))
        session.add(
            EpisodeSound(
                assembly_id=assembly.id,
                row_version=1,
                document=SoundDocument().model_dump(mode="json"),
                reviewed_timeline_hash=reviewed_hash,
                updated_at=utcnow(),
            )
        )
        session.commit()
        f.storyboard.create(
            f.project,
            f.episode,
            {
                "storyboard_version": f.storyboard.get(f.project, f.episode, f.shot_id)[
                    "storyboard_version"
                ],
                "script": "Reserved shot",
                "duration_ms": 5000,
            },
            "sound-reserved-source",
        )
        latest = svc.get(f.project, f.episode)
        state = svc.sync(f.project, f.episode, export_body(latest))
        sound = EpisodeSoundService(session, svc.settings).get(f.project, f.episode)
        assert sound["timeline_hash"] == reviewed_hash
        assert sound["duration_ms"] == 4200 and sound["needs_review"] is False
        job = svc.export(f.project, f.episode, export_body(state), "reviewed-sound-reserved")
        assert len(job["timeline"]) == 1


def make_sample(path, color, audio=False):
    args = [
        executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=320x240:r=24:d=1.4",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1.4"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1.4", str(path)]
    subprocess.run(args, check=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("aspect,resolution", [("16:9", "720p"), ("9:16", "1080p")])
def test_actual_ffmpeg_trim_concat_audio_and_shape(tmp_path, aspect, resolution):
    try:
        executable("ffmpeg")
    except RuntimeError:
        pytest.skip("Install FFmpeg for real render acceptance")
    settings = Settings(_env_file=None)
    renderer = VideoRenderer(settings)
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make_sample(first, "red", True)
    make_sample(second, "blue")
    clips = [
        {"media_id": "1", "trim_in_ms": 200, "trim_out_ms": 1200, "muted": False},
        {"media_id": "2", "trim_in_ms": 0, "trim_out_ms": 1000, "muted": True},
    ]
    output, info = renderer.render(
        {"aspect": aspect, "resolution": resolution, "clips": clips},
        {"1": first, "2": second},
        tmp_path,
    )
    assert output.stat().st_size > 1000
    assert abs(info["duration_ms"] - 2000) < 200
    assert info["has_audio"]
    assert (info["width"], info["height"]) == ((1280, 720) if aspect == "16:9" else (1080, 1920))
    calls = []

    def cancel(*_):
        calls.append(True)
        if len(calls) > 1:
            raise RenderCancelled()

    with pytest.raises(RenderCancelled):
        VideoRenderer(settings, cancel).run(
            [executable("ffmpeg"), "-re", "-f", "lavfi", "-i", "color=d=30", "-f", "null", "-"],
            tmp_path,
            stage="rendering",
            progress=0,
        )
