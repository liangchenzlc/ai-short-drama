"""Decode actual files to compare browser proxy frames against both render modes."""

import subprocess
from array import array

import pytest

from short_drama.core.config import Settings
from short_drama.service.creation.assembly.rendering.renderer import VideoRenderer, executable


def test_multitrack_still_composition_subtitles_and_real_audio_analysis(tmp_path, monkeypatch):
    from PIL import Image

    from short_drama.schemas.creation.sound.tracks.sound import AudioClip, SoundDocument, Subtitle
    from short_drama.service.creation.assembly.editing.audio import analyse_audio
    from short_drama.service.creation.sound.tracks.render import font_metadata

    video = tmp_path / "voice.mp4"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    image = tmp_path / "still.png"
    Image.new("RGB", (90, 160), "blue").save(image)
    renderer = VideoRenderer(Settings())
    sound = SoundDocument(
        schema_version=2,
        burn_subtitles=True,
        subtitles=[Subtitle(id="caption", start_ms=1100, end_ms=1900, text="真实字幕校对")],
        audio_clips=[
            AudioClip(
                id="voice",
                kind="original",
                media_id="1",
                start_ms=0,
                duration_ms=1000,
                trim_in_ms=0,
                trim_out_ms=1000,
                volume=1,
                fade_in_ms=0,
                fade_out_ms=0,
                ducking=False,
            ),
            AudioClip(
                id="music",
                kind="music",
                media_id="1",
                start_ms=1000,
                duration_ms=1000,
                trim_in_ms=0,
                trim_out_ms=500,
                loop=True,
                volume=0.5,
                fade_in_ms=50,
                fade_out_ms=50,
                ducking=False,
            ),
        ],
    ).model_dump(mode="json")
    clip = {"media_id": "1", "trim_in_ms": 0, "trim_out_ms": 1000, "muted": False}
    snapshot = {
        "version": 2,
        "resolution": "preview",
        "aspect": "16:9",
        "clips": [
            clip,
            {
                **clip,
                "media_id": "2",
                "source_type": "image",
                "duration_ms": 1000,
                "width": 90,
                "height": 160,
                "transform": {"fit": "cover", "scale": 1.2, "x": 0.2, "y": 0.7},
            },
        ],
        "sound": {"version": 2, "document": sound, "media": [], **font_metadata(renderer.settings)},
    }
    output, metadata = renderer.render(snapshot, {"1": video, "2": image}, tmp_path)
    assert abs(metadata["duration_ms"] - 2000) <= 34
    assert metadata["has_audio"] and metadata["width"] == 640
    colors = decode_colors(output)
    assert colors[5][0] > 200 and colors[40][2] > 150
    assert "真实字幕校对" in (tmp_path / "subtitles.ass").read_text(encoding="utf-8")
    result = analyse_audio(renderer, output, tmp_path)
    assert 1 <= len(result["waveform"]) <= 1024
    assert max(result["waveform"]) > 0.01
    assert result["integrated_lufs"] is not None

    # Decode and mix real sources; only the external recognition model is a substitute.
    import wave

    import short_drama.service.creation.assembly.materials.media_execute as assembly_media_execute
    from short_drama.schemas.canvas.tools.transcription.transcription import (
        CanvasTranscriptionResult,
    )

    class Recognizer:
        def __init__(self, *_):
            pass

        def transcribe(self, path, language):
            with wave.open(str(path)) as audio:
                assert audio.getframerate() == 16000 and audio.getnchannels() == 1
                samples = array("h")
                samples.frombytes(audio.readframes(audio.getnframes()))
                assert max(abs(v) for v in samples[:8000]) > 100
                assert max(abs(v) for v in samples[-4000:]) == 0  # music is excluded
            return CanvasTranscriptionResult(
                segments=[{"start_ms": 100, "end_ms": 1800, "text": "你好世界"}],
                srt="",
                language=language,
            )

    monkeypatch.setattr(assembly_media_execute, "CanvasTranscriptionExecutor", Recognizer)
    ranges = [
        {"clip_id": "a", "start_ms": 0, "end_ms": 1000, "source_in_ms": 0},
        {"clip_id": "b", "start_ms": 1000, "end_ms": 2000, "source_in_ms": 0},
    ]
    snapshot["transcription"] = {
        "ranges": ranges,
        "start_ms": 0,
        "end_ms": 2000,
        "language": "zh",
        "source_hash": "frozen",
        "voice_context_hash": "voices",
    }
    recognized = assembly_media_execute.execute_media_job(
        renderer, snapshot, "transcription", {"1": video}, tmp_path
    )
    assert "".join(s["text"] for s in recognized["subtitles"]) == "你好世界"
    assert [s["anchor_clip_id"] for s in recognized["subtitles"]] == ["a", "b"]
    assert recognized["voice_context_hash"] == "voices"
    snapshot["sound"]["document"]["tracks"] = [{"id": "original", "muted": True}]
    with pytest.raises(RuntimeError, match="有效人声"):
        assembly_media_execute.execute_media_job(
            renderer, snapshot, "transcription", {"1": video}, tmp_path
        )


def decode_colors(path):
    result = subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return list(zip(*[iter(result.stdout)] * 3, strict=True))


@pytest.mark.parametrize("muted", ["none", "clip", "track"])
def test_multitrack_music_ducks_union_of_explicit_and_audible_dialogue(tmp_path, muted):
    import math

    from short_drama.schemas.creation.sound.tracks.sound import AudioClip, SoundDocument
    from short_drama.service.creation.assembly.editing.audio import audible_clips, render_audio

    renderer = VideoRenderer(Settings())
    sources = {}
    for key, frequency in (("1", 200), ("2", 600)):
        path = tmp_path / f"{key}.wav"
        subprocess.run(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration=2",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        sources[key] = path
    document = SoundDocument(
        schema_version=2,
        native_ducking=[{"start_ms": 200, "end_ms": 600, "text": "explicit"}],
        tracks=[{"id": "dialogue", "muted": muted == "track"}],
        audio_clips=[
            AudioClip(
                id="music",
                media_id="1",
                kind="music",
                duration_ms=2000,
                trim_out_ms=2000,
                volume=1,
                fade_in_ms=0,
                fade_out_ms=0,
                ducking=True,
            ),
            AudioClip(
                id="voice",
                media_id="2",
                kind="dialogue",
                start_ms=1000,
                duration_ms=400,
                trim_out_ms=400,
                volume=1,
                fade_in_ms=0,
                fade_out_ms=0,
                muted=muted == "clip",
            ),
        ],
    ).model_dump(mode="json")
    output = render_audio(renderer, audible_clips(document), sources, tmp_path, 2000, document)
    decoded = subprocess.run(
        [executable("ffmpeg"), "-v", "error", "-i", str(output), "-ac", "1", "-f", "f32le", "-"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    samples = array("f", decoded.stdout)

    def music_energy(start):
        values = samples[int(start * 48000) : int((start + 0.1) * 48000)]
        return abs(
            sum(
                value
                * complex(
                    math.cos(2 * math.pi * 200 * i / 48000),
                    math.sin(2 * math.pi * 200 * i / 48000),
                )
                for i, value in enumerate(values)
            )
        )

    baseline = music_energy(0.75)
    assert music_energy(0.35) / baseline == pytest.approx(0.25, abs=0.03)
    assert music_energy(1.15) / baseline == pytest.approx(0.25 if muted == "none" else 1, abs=0.03)


@pytest.mark.parametrize("phase", [350, 1350])
def test_loop_split_keeps_real_audio_phase(tmp_path, phase):
    from short_drama.schemas.creation.sound.tracks.sound import AudioClip
    from short_drama.service.creation.assembly.editing.audio import render_audio

    source = tmp_path / "pulse.wav"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0.3*sin(2*PI*600*t)*between(t\\,0.2\\,0.4):s=48000:d=1",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    renderer = VideoRenderer(Settings())
    whole = AudioClip(
        id="music",
        media_id="1",
        duration_ms=2000,
        trim_out_ms=1000,
        volume=1,
        loop=True,
        fade_in_ms=0,
        fade_out_ms=0,
        ducking=False,
    ).model_dump(mode="json")
    full = render_audio(renderer, [whole], {"1": source}, tmp_path, 2000, prefix="whole")
    split = render_audio(
        renderer,
        [
            {**whole, "duration_ms": 350},
            {
                **whole,
                "id": "second",
                "start_ms": 350,
                "duration_ms": 1650,
                "loop_offset_ms": phase,
            },
        ],
        {"1": source},
        tmp_path,
        2000,
        prefix="split",
    )

    def decode(path):
        result = subprocess.run(
            [executable("ffmpeg"), "-v", "error", "-i", str(path), "-f", "f32le", "-"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return array("f", result.stdout)

    expected, actual = decode(full), decode(split)
    assert len(actual) == len(expected) == 2000 * 48 * 2
    assert max(abs(a - b) for a, b in zip(actual, expected, strict=True)) < 0.00001


@pytest.mark.parametrize("rate", ["24", "30000/1001", "vfr"])
def test_trimmed_export_matches_proxy_frames_at_non_native_boundaries(tmp_path, rate):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"nullsrc=s=160x90:r={'30' if rate == 'vfr' else rate}:d=2,format=rgb24,"
            "geq=r='mod(N*43,256)':g='mod(N*97,256)':b='mod(N*17,256)'",
            *(["-vf", "select='not(eq(mod(n,3),1))'", "-fps_mode", "vfr"] if rate == "vfr" else []),
            "-c:v",
            "libx264",
            "-crf",
            "12",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    renderer = VideoRenderer(Settings())
    proxy, _, _ = renderer.preview_assets(source, renderer.probe(source), tmp_path)
    frames = decode_colors(proxy)
    cuts = [(4, 13), (19, 24), (37, 38)]
    expected = [color for start, end in cuts for color in frames[start:end]]
    for resolution in ("preview", "720p", "1080p"):
        directory = tmp_path / resolution
        directory.mkdir()
        output, metadata = renderer.render(
            {
                "version": 2,
                "resolution": resolution,
                "aspect": "16:9",
                "fps": 30,
                "clips": [
                    {
                        "media_id": "1",
                        "trim_in_ms": round(start * 1000 / 30),
                        "trim_out_ms": round(end * 1000 / 30),
                        "muted": False,
                    }
                    for start, end in cuts
                ],
            },
            {"1": source},
            directory,
        )
        actual = decode_colors(output)
        assert len(actual) == len(expected) == 15
        assert abs(metadata["duration_ms"] - 500) <= 34
        for index, (rendered, preview) in enumerate(zip(actual, expected, strict=True)):
            assert max(abs(a - b) for a, b in zip(rendered, preview, strict=True)) <= 8, (
                resolution,
                rate,
                index,
                rendered,
                preview,
            )


def test_trimmed_audio_pulse_stays_synchronized_and_muted_segments_stay_silent(tmp_path):
    source = tmp_path / "pulse.mp4"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=30:d=1,drawbox=color=lime:t=fill:enable='between(t,0.5,0.74)'",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0.3*sin(2*PI*600*t)*between(t\\,0.5\\,0.75):s=44100:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    renderer = VideoRenderer(Settings())
    output, metadata = renderer.render(
        {
            "version": 2,
            "resolution": "preview",
            "aspect": "16:9",
            "fps": 30,
            "clips": [
                {"media_id": "1", "trim_in_ms": 400, "trim_out_ms": 900, "muted": muted}
                for muted in (False, True)
            ],
        },
        {"1": source},
        tmp_path,
    )
    decoded = subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-i",
            str(output),
            "-map",
            "0:a:0",
            "-t",
            "1",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    samples = array("f", decoded.stdout)
    assert len(samples) == 48000
    assert abs(metadata["duration_ms"] - 1000) <= 34

    def energy(start, end):
        window = samples[round(start * 48000) : round(end * 48000)]
        return sum(sample * sample for sample in window) / len(window)

    assert energy(0.02, 0.07) < 0.00001
    assert energy(0.13, 0.30) > 0.005
    assert energy(0.4, 0.48) < 0.00001
    assert energy(0.52, 0.98) < 0.00001
    colors = decode_colors(output)
    assert len(colors) == 30
    assert colors[2][1] < 10 and colors[3][1] > 200
    assert colors[17][1] < 10 and colors[18][1] > 200
