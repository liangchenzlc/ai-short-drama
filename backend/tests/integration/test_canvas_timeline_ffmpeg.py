"""显式启用的真实 FFmpeg 专项；不依赖数据库或收费供应商。"""

import json
import math
import os
import shutil
import subprocess
from array import array

import pytest

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_timeline import CanvasTimelinePlanRequest
from short_drama.service import canvas_timeline_renderer as module
from short_drama.service.canvas_timeline_compile import compile_timeline_plan
from short_drama.service.canvas_timeline_native import SUBTITLE_FILE_NAME
from short_drama.service.canvas_timeline_renderer import CanvasTimelineRenderer
from short_drama.service.video_render import RenderCancelled, executable

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_TIMELINE_FFMPEG") != "1",
    reason="RUN_CANVAS_TIMELINE_FFMPEG=1 is required for real media acceptance",
)


def ffmpeg(*args):
    return subprocess.run(
        [executable("ffmpeg"), "-v", "error", "-y", *map(str, args)],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("timeline-media")
    video = root / "source.mkv"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x180:r=30:d=6",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=220:duration=6",
        "-c:v",
        "libx264",
        "-c:a",
        "pcm_s16le",
        video,
    )
    files = {"video": video}
    for name, frequency in (("voice", 440), ("bgm", 880)):
        path = root / f"{name}.wav"
        ffmpeg("-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=6", path)
        files[name] = path
    for name, color, duration, tone in (
        ("tone", "red", 1, True),
        ("silent", "green", 1, False),
        ("short", "red", 0.9, False),
    ):
        path = root / f"{name}.mp4"
        args = ["-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=30:d={duration}"]
        if tone:
            args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
        ffmpeg(*args, "-c:v", "libx264", path)
        files[name] = path
    still = root / "still.png"
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x180", "-frames:v", "1", still)
    files["still"] = still
    broken = root / "broken.mp4"
    broken.write_bytes(b"not a video")
    files["broken"] = broken
    return files


def clip(identifier, kind, start, duration, source, **extra):
    return {
        "id": identifier,
        "kind": kind,
        "trackId": "a" if kind == "audio" else "v",
        "startMs": start,
        "durationMs": duration,
        "directMedia": {"storageKey": f"resource:{source}"},
        **extra,
    }


def request(clips, duration, **options):
    return CanvasTimelinePlanRequest.model_validate(
        {
            "timeline": {
                "version": 2,
                "durationMs": duration,
                "tracks": [{"id": "v"}, {"id": "a"}, {"id": "s"}],
                "clips": clips,
            },
            "options": {"width": 320, "height": 180, **options},
        }
    )


def render(tmp_path, media, value, **settings):
    renderer = CanvasTimelineRenderer(
        Settings(_env_file=None, render_scratch_root=str(tmp_path / "scratch"), **settings)
    )
    compiled = compile_timeline_plan(value)
    previous = compiled.model_dump()
    (tmp_path / "request.json").write_text(
        value.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    (tmp_path / "sources.json").write_text(
        json.dumps({key: str(path) for key, path in media.items()}), encoding="utf-8"
    )
    with renderer.render(compiled, lambda identifier: media[identifier].open("rb")) as result:
        target = tmp_path / "accepted.mp4"
        shutil.copyfile(result.path, target)
        assert result.size == target.stat().st_size > 0
        assert (result.width, result.height) == (compiled.output.width, compiled.output.height)
        assert compiled.model_dump() == previous
    assert not list((tmp_path / "scratch").iterdir())
    return target, result, renderer


def samples(output):
    return array("f", ffmpeg("-i", output, "-vn", "-ac", "1", "-ar", "44100", "-f", "f32le", "-"))


def amplitude(pcm, start, frequency, seconds=0.5):
    offset, count = round(start * 44100), round(seconds * 44100)
    window = pcm[offset : offset + count]
    assert len(window) == count
    real = sum(
        value * math.cos(2 * math.pi * frequency * i / 44100) for i, value in enumerate(window)
    )
    imag = sum(
        value * math.sin(2 * math.pi * frequency * i / 44100) for i, value in enumerate(window)
    )
    return 2 * math.hypot(real, imag) / count


def frame_colors(output):
    raw = ffmpeg(
        "-i", output, "-an", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
    )
    return [tuple(raw[i : i + 3]) for i in range(0, len(raw), 3)]


def record(tmp_path, **metrics):
    (tmp_path / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")


@pytest.mark.parametrize("muted", [False, True])
def test_six_seconds_original_voice_bgm_chinese_and_zero_volume(tmp_path, media, muted):
    clips = [
        clip(f"v{i}", "video", i * 2000, 2000, "video", sourceStartMs=i * 2000) for i in range(3)
    ]
    if muted:
        clips = [{**item, "volume": 0} for item in clips]
    clips += [
        clip("voice", "audio", 1000, 2000, "voice", sourceStartMs=1000, volume=0 if muted else 1),
        clip("bgm", "audio", 0, 6000, "bgm", volume=0.2),
        {
            "id": "sub",
            "kind": "subtitle",
            "trackId": "s",
            "startMs": 1000,
            "durationMs": 2000,
            "text": "中文成片验证",
        },
    ]
    value = request(clips, 6000, width=1920, height=1080)
    if muted:
        value.timeline.tracks[1].muted = True
    output, result, _ = render(tmp_path, media, value, render_timeout_seconds=30)
    assert abs(result.duration_ms - 6000) <= 80
    pcm = samples(output)
    assert abs(len(pcm) / 44100 - 6) < 0.06
    measured = []
    for start in (0.25, 1.25, 2.25, 3.25, 5.25):
        base, voice, bgm = [amplitude(pcm, start, frequency) for frequency in (220, 440, 880)]
        measured.append({"start": start, "base": base, "voice": voice, "bgm": bgm})
        if muted:
            assert max(abs(value) for value in pcm) < 0.0001
        else:
            assert base > 0.06 and 0.008 < bgm < base * 0.35
            assert voice > 0.06 if 1 <= start < 3 else voice < 0.003
    pixels = ffmpeg(
        "-ss",
        "1.5",
        "-i",
        output,
        "-frames:v",
        "1",
        "-vf",
        "crop=1920:250:0:830",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "-",
    )
    white = sum(
        all(channel > 180 for channel in pixels[i : i + 3]) for i in range(0, len(pixels), 3)
    )
    assert white > 100 and "中文成片验证" in result.subtitle_srt
    frames = frame_colors(output)
    assert len(frames) == 180
    record(
        tmp_path, duration_ms=result.duration_ms, frames=len(frames), white=white, tones=measured
    )


def test_gap_silent_video_and_fade_in_out(tmp_path, media):
    output, result, _ = render(
        tmp_path,
        media,
        request(
            [
                clip("tone", "video", 0, 1000, "tone", volume=0),
                clip("silent", "video", 2000, 1000, "silent"),
                clip("voice", "audio", 0, 1000, "bgm", fadeInMs=800, fadeOutMs=100),
            ],
            3000,
        ),
        render_timeout_seconds=30,
    )
    pcm = samples(output)
    early, late, fading = [amplitude(pcm, start, 880, 0.02) for start in (0.02, 0.7, 0.97)]
    assert early < late * 0.25 and fading < late * 0.6
    assert max(abs(value) for value in pcm[round(1.2 * 44100) : round(2.8 * 44100)]) < 0.002
    frames = frame_colors(output)
    assert len(frames) == 90
    assert all(red > 180 and green < 40 and blue < 40 for red, green, blue in frames[:30])
    assert all(max(pixel) < 40 for pixel in frames[30:60])
    assert all(green > 90 and red < 40 and blue < 40 for red, green, blue in frames[60:])
    record(tmp_path, duration_ms=result.duration_ms, frames=frames, fade=[early, late, fading])


def test_image_audio_subtitle_and_trailing_gap_finish(tmp_path, media):
    output, result, _ = render(
        tmp_path,
        media,
        request(
            [
                clip("still", "image", 0, 2000, "still"),
                clip("voice", "audio", 0, 2000, "voice"),
                {
                    "id": "sub",
                    "kind": "subtitle",
                    "trackId": "s",
                    "startMs": 500,
                    "durationMs": 1000,
                    "text": "图片",
                },
            ],
            3000,
        ),
        render_timeout_seconds=30,
    )
    pcm, frames = samples(output), frame_colors(output)
    assert amplitude(pcm, 0.5, 440) > 0.06
    assert max(abs(value) for value in pcm[round(2.3 * 44100) : round(2.8 * 44100)]) < 0.002
    assert len(frames) == 90 and frames[10][2] > 180 and max(frames[75]) < 40
    record(tmp_path, duration_ms=result.duration_ms, frames=frames)


def test_100ms_source_slack_holds_last_frame_without_shifting_end(tmp_path, media):
    output, result, _ = render(
        tmp_path, media, request([clip("short", "video", 0, 1000, "short")], 1000)
    )
    frames = frame_colors(output)
    assert len(frames) == 30 and all(red > 180 and blue < 40 for red, _, blue in frames)
    assert abs(result.duration_ms - 1000) < 80
    record(tmp_path, duration_ms=result.duration_ms, frames=frames)


def test_half_second_one_fps_uses_legal_frame_tolerance(tmp_path, media):
    output, result, _ = render(
        tmp_path, media, request([clip("short", "video", 0, 500, "silent")], 500, fps=1)
    )
    frames = frame_colors(output)
    assert len(frames) == 1 and 500 <= result.duration_ms <= 1100
    record(tmp_path, duration_ms=result.duration_ms, frames=frames)


@pytest.mark.parametrize(
    "clips, message, error_type",
    [
        ([clip("broken", "video", 0, 1000, "broken")], "媒体音轨探测失败", RuntimeError),
        ([clip("wrong", "video", 0, 1000, "voice")], "缺少所需音视频轨", WorkflowError),
        (
            [clip("video", "video", 0, 1000, "silent"), clip("audio", "audio", 0, 1000, "silent")],
            "没有可用音轨",
            WorkflowError,
        ),
        ([clip("short", "video", 0, 1001, "short")], "素材时长不足", WorkflowError),
    ],
)
def test_real_source_failures_are_rejected_and_cleaned(tmp_path, media, clips, message, error_type):
    with pytest.raises(error_type, match=message):
        render(tmp_path, media, request(clips, 1001))
    assert not list((tmp_path / "scratch").iterdir())


def test_missing_subtitle_file_is_a_real_encoder_failure(tmp_path, media, monkeypatch):
    renderer = CanvasTimelineRenderer(Settings(_env_file=None, render_scratch_root=str(tmp_path)))
    original = renderer._run

    def remove_subtitle(arguments, directory, burn):
        (directory / SUBTITLE_FILE_NAME).unlink()
        return original(arguments, directory, burn)

    monkeypatch.setattr(renderer, "_run", remove_subtitle)
    compiled = compile_timeline_plan(
        request(
            [
                clip("video", "video", 0, 1000, "video"),
                {
                    "id": "sub",
                    "kind": "subtitle",
                    "trackId": "s",
                    "durationMs": 1000,
                    "text": "中文",
                },
            ],
            1000,
        )
    )
    with pytest.raises(RuntimeError, match="ffmpeg 时间线渲染失败"):
        with renderer.render(compiled, lambda identifier: media[identifier].open("rb")):
            pytest.fail("missing subtitle must not be accepted")
    assert not list(tmp_path.iterdir())


def test_cancel_stops_actual_ffmpeg_and_removes_all_temporary_files(tmp_path, media, monkeypatch):
    original = module.subprocess.Popen
    encoders = []

    def launch(arguments, *args, **kwargs):
        process = original(arguments, *args, **kwargs)
        if "-filter_complex" in arguments:
            encoders.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", launch)

    def cancel(stage, _percent):
        if stage == "正在渲染…":
            raise RenderCancelled("用户取消时间线")

    renderer = CanvasTimelineRenderer(
        Settings(_env_file=None, render_scratch_root=str(tmp_path)), cancel
    )
    compiled = compile_timeline_plan(request([clip("video", "video", 0, 6000, "video")], 6000))
    with pytest.raises(RenderCancelled, match="用户取消时间线"):
        with renderer.render(compiled, lambda identifier: media[identifier].open("rb")):
            pytest.fail("cancelled timeline must not return media")
    assert len(encoders) == 1 and encoders[0].poll() is not None and encoders[0].stdout.closed
    assert not list(tmp_path.iterdir())


def test_actual_encode_cannot_exceed_scratch_budget(tmp_path, media):
    renderer = CanvasTimelineRenderer(
        Settings(
            _env_file=None,
            render_scratch_root=str(tmp_path),
            render_max_scratch_bytes=media["video"].stat().st_size + 1024,
        )
    )
    compiled = compile_timeline_plan(request([clip("video", "video", 0, 6000, "video")], 6000))
    with pytest.raises(RuntimeError, match="临时空间达到上限"):
        with renderer.render(compiled, lambda identifier: media[identifier].open("rb")):
            pytest.fail("oversized encode must not return media")
    assert not list(tmp_path.iterdir())
