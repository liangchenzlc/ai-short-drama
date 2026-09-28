import subprocess

import pytest
from generation_fixtures import generation_session
from sqlalchemy import select
from test_shot_video_generation import current, setup_video

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import EpisodeRenderJob, MediaFile, ShotVideo
from short_drama.service.episode_assembly_service import EpisodeAssemblyService
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


def test_missing_video_blocks_export_and_sync_resets_replaced_trim():
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
        assert synced["clips"][0]["trim_in_ms"] == 0
        assert synced["clips"][0]["issue"] == "preparing"
        with pytest.raises(WorkflowError):
            svc.export(f.project, f.episode, export_body(synced), "preparing")
        assert synced["jobs"][0]["kind"] == "probe"


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
