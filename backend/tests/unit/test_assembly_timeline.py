import subprocess
from array import array
from uuid import uuid4

import pytest
from generation_fixtures import generation_session
from PIL import Image
from test_episode_assembly import body, export_body, flow, make_sample

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import EpisodeRenderJob
from short_drama.service.video_render import VideoRenderer, executable


def test_split_delete_undo_reorder_reload_and_idempotent_save():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        original = state["clips"][0]["id"]
        payload = body(state)
        second = str(uuid4())
        payload["request_id"] = "split-1"
        payload["clips"][0]["trim_out_ms"] = 2000
        payload["clips"].append(
            {
                **payload["clips"][0],
                "id": second,
                "source_clip_id": original,
                "trim_in_ms": 2000,
                "trim_out_ms": 4200,
            }
        )
        state = svc.edit(f.project, f.episode, payload)
        assert len(state["clips"]) == 2
        assert len(state["sources"]) == 1
        assert (
            svc.edit(f.project, f.episode, payload)["assembly"]["row_version"]
            == state["assembly"]["row_version"]
        )
        assert svc.get(f.project, f.episode)["clips"][1]["id"] == second
        original_edits = body(state)["clips"]
        payload = body(state)
        payload["clips"] = [payload["clips"][1]]
        state = svc.edit(f.project, f.episode, payload)
        assert [c["id"] for c in state["clips"]] == [second]
        state = svc.sync(f.project, f.episode, export_body(state))
        assert [c["id"] for c in state["clips"]] == [second]
        assert state["clips"][0]["trim_in_ms"] == 2000
        state = svc.edit(f.project, f.episode, {**body(state), "clips": original_edits[::-1]})
        assert [c["id"] for c in state["clips"]] == [second, original]
        job = svc.export(f.project, f.episode, export_body(state), "split-export")
        row = session.get(EpisodeRenderJob, int(job["id"]))
        assert [c["clip_id"] for c in row.snapshot["clips"]] == [second, original]
        assert row.snapshot["version"] == 2
        session.commit()
        state = svc.edit(f.project, f.episode, {**body(state), "clips": []})
        assert state["clips"] == [] and len(state["sources"]) == 1
        assert svc.sync(f.project, f.episode, export_body(state))["clips"] == []


def test_unknown_source_and_subframe_edits_are_rejected_atomically():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        payload = body(state)
        payload["clips"].append(
            {**payload["clips"][0], "id": str(uuid4()), "source_clip_id": "999999"}
        )
        with pytest.raises(WorkflowError):
            svc.edit(f.project, f.episode, payload)
        assert (
            svc.get(f.project, f.episode)["assembly"]["row_version"]
            == state["assembly"]["row_version"]
        )
        payload = body(state)
        payload["clips"][0]["trim_out_ms"] = 1
        with pytest.raises(WorkflowError):
            svc.edit(f.project, f.episode, payload)


def test_preview_reuses_matching_snapshot_and_does_not_become_export():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        first = svc.export(f.project, f.episode, export_body(state), "preview-1", kind="preview")
        second = svc.export(f.project, f.episode, export_body(state), "preview-2", kind="preview")
        assert first["id"] == second["id"]
        assert first["kind"] == "preview"
        assert svc.jobs(f.project, f.episode)["items"] == []
        assert first["timeline"][0]["trim_out_ms"] == 4200


def test_many_previews_keep_last_successful_export_available():
    with generation_session() as session:
        f, svc = flow(session)
        state = svc.initialize(f.project, f.episode)
        exported = svc.export(f.project, f.episode, export_body(state), "old-export")
        row = session.get(EpisodeRenderJob, int(exported["id"]))
        row.status = "succeeded"
        row.output_media_id = 201
        session.commit()
        for index in range(31):
            preview = svc.export(
                f.project, f.episode, export_body(state), f"preview-{index}", kind="preview"
            )
            session.get(EpisodeRenderJob, int(preview["id"])).status = "cancelled"
            session.commit()
        jobs = svc.get(f.project, f.episode)["jobs"]
        assert any(j["id"] == exported["id"] and j["media_id"] == "201" for j in jobs)


def test_precise_render_many_short_slices_preserves_frame_count_and_audio(tmp_path):
    make_sample(tmp_path / "source.mp4", "red", True)
    renderer = VideoRenderer(Settings())
    boundaries = [0, 133, 267, 400, 533, 667, 800]
    snapshot = {
        "version": 2,
        "resolution": "preview",
        "aspect": "16:9",
        "fps": 30,
        "clips": [
            {"media_id": "1", "trim_in_ms": a, "trim_out_ms": b, "muted": i % 2 == 1}
            for i, (a, b) in enumerate(zip(boundaries, boundaries[1:], strict=False))
        ],
    }
    output, info = renderer.render(snapshot, {"1": tmp_path / "source.mp4"}, tmp_path)
    assert abs(info["duration_ms"] - 800) <= 34
    assert info["has_audio"]
    decoded = subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(output),
            "-map",
            "0:v:0",
            "-f",
            "framemd5",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    frames = [
        line for line in decoded.stdout.decode().splitlines() if line and not line.startswith("#")
    ]
    assert len(frames) == 24
    audio = subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(output),
            "-map",
            "0:a:0",
            "-t",
            "0.8",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    samples = array("f", audio.stdout)
    assert len(samples) == 38400

    def energy(start, end):
        window = samples[int(start * 48000) : int(end * 48000)]
        return sum(v * v for v in window) / len(window)

    assert energy(0.03, 0.10) > 0.001
    assert energy(0.17, 0.23) < 0.00001
    proxy, thumbnail, filmstrip = renderer.preview_assets(
        tmp_path / "source.mp4", renderer.probe(tmp_path / "source.mp4"), tmp_path
    )
    assert renderer.probe(proxy)["fps"] in ("30/1", "30")
    assert thumbnail.read_bytes()[:2] == b"\xff\xd8"
    with Image.open(filmstrip) as strip:
        assert strip.size == (320, 90)


def test_filmstrip_contains_different_sampled_frames(tmp_path):
    source = tmp_path / "moving.mp4"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30:duration=2.4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    renderer = VideoRenderer(Settings())
    metadata = renderer.probe(source)
    _, _, filmstrip = renderer.preview_assets(source, metadata, tmp_path)
    assert metadata["filmstrip_count"] == 3
    assert metadata["filmstrip_interval_ms"] == 800
    with Image.open(filmstrip) as strip:
        assert strip.size == (480, 90)
        assert len({strip.crop((i * 160, 0, (i + 1) * 160, 90)).tobytes() for i in range(3)}) == 3
