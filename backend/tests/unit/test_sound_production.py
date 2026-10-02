import base64
import copy
import subprocess
from array import array

import pytest
from pydantic import ValidationError

from short_drama.ai.adapters import build_submission, select_adapter
from short_drama.ai.gateway import GenerationGateway
from short_drama.ai.types import GenerationError
from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.episode_sound import SoundDocument
from short_drama.service.audio_media import inspect_audio
from short_drama.service.episode_sound_service import line_hash, validate_sound_snapshot
from short_drama.service.sound_render import font_metadata
from short_drama.service.subtitles import export_srt, parse_srt
from short_drama.service.video_render import VideoRenderer, executable


def test_speech_adapter_endpoint_binary_output_and_unknown_acceptance():
    settings = Settings(_env_file=None)
    gateway = GenerationGateway(settings)
    snapshot = {
        "service_type": "audio",
        "model_key": "manual-voice-model",
        "base_url": "https://speech.example/v1/audio/speech",
    }
    request = {"input": {"text": "你好，世界。"}, "parameters": {"voice": "speaker-1"}}
    adapter = select_adapter(snapshot)
    url, _, body = build_submission(snapshot, request, adapter)
    assert url == snapshot["base_url"]
    assert body == {
        "model": "manual-voice-model",
        "input": "你好，世界。",
        "voice": "speaker-1",
        "response_format": "wav",
    }
    sent = []

    def send(*args, **kwargs):
        sent.append(kwargs)
        return 200, {"Content-Type": "audio/wav"}, b"test-audio"

    gateway.transport.request = send
    result = gateway.submit(snapshot, request, "secret")
    assert base64.b64decode(result.outputs[0]["base64"]) == b"test-audio"
    assert sent[0]["headers"]["Authorization"] == "Bearer secret"
    gateway.transport.request = lambda *a, **k: (503, {}, b"secret must not escape")
    with pytest.raises(GenerationError) as failure:
        gateway.submit(snapshot, request, "secret")
    assert failure.value.accepted_unknown and not failure.value.retryable
    assert "secret" not in str(failure.value)


def test_srt_roundtrip_unicode_and_reject_overlap_or_out_of_order():
    value = (
        "\ufeff1\r\n00:00:00,125 --> 00:00:01,234\r\n你好，世界！\r\n第二行\r\n\r\n"
        "2\r\n00:00:01,234 --> 00:00:02,000\r\n再见。\r\n"
    )
    subtitles = parse_srt(value)
    assert parse_srt(export_srt([s.model_dump() for s in subtitles])) == subtitles
    with pytest.raises(ValueError):
        parse_srt(value.replace("00:00:01,234 -->", "00:00:01,000 -->"))
    with pytest.raises(ValueError):
        parse_srt(value.replace("00:00:00,125", "00:61:00,125"))
    with pytest.raises(ValidationError):
        SoundDocument(original_volume=float("nan"))


def test_export_rejects_unreviewed_stale_and_out_of_bounds_audio():
    line = {
        "id": "a",
        "character": "A",
        "text": "hello",
        "voice": "v",
        "config_id": "1",
        "media_id": "2",
        "start_ms": 0,
    }
    line["adopted_hash"] = line_hash(line)
    snapshot = {
        "clips": [{"trim_in_ms": 0, "trim_out_ms": 1000}],
        "sound": {
            "needs_review": False,
            "document": SoundDocument(dialogue=[line]).model_dump(mode="json"),
            "media": [{"media_id": "2", "duration_ms": 1000}],
        },
    }
    validate_sound_snapshot(snapshot)
    for change, code in [
        (lambda s: s["sound"].update(needs_review=True), "sound_review_required"),
        (
            lambda s: s["sound"]["document"]["dialogue"][0].update(text="changed"),
            "audio_dialogue_stale",
        ),
        (lambda s: s["sound"]["document"]["dialogue"][0].update(start_ms=1_000), "audio_timing"),
    ]:
        changed = copy.deepcopy(snapshot)
        change(changed)
        with pytest.raises(WorkflowError) as error:
            validate_sound_snapshot(changed)
        assert error.value.code == code


def run_ffmpeg(*args):
    return subprocess.run(
        [executable("ffmpeg"), "-v", "error", "-y", *map(str, args)],
        capture_output=True,
        check=True,
        timeout=30,
    )


@pytest.mark.parametrize("burn", [False, True])
def test_real_audio_decode_mix_preserves_frames_timing_and_subtitles(tmp_path, burn):
    from pathlib import Path

    video, speech, music = [tmp_path / name for name in ("base.mp4", "speech.wav", "music.wav")]
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x180:r=30:d=2",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-t",
        "2",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        video,
    )
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=600:sample_rate=44100:duration=0.5", speech)
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=200:sample_rate=48000:duration=0.4", music)
    settings = Settings(_env_file=None)
    assert inspect_audio(speech, settings)["duration_ms"] == 500
    font = next(
        (
            p
            for p in [
                Path("C:/Windows/Fonts/msyh.ttc"),
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            ]
            if p.exists()
        ),
        None,
    )
    if burn and font is None:
        pytest.fail("A Chinese font is required for the audio/subtitle acceptance test")
    if font:
        settings.render_subtitle_font_path = str(font)
        settings.render_subtitle_font_family = (
            "Microsoft YaHei" if font.name == "msyh.ttc" else "Noto Sans CJK SC"
        )
    line = {
        "id": "a",
        "character": "A",
        "text": "你好，世界",
        "voice": "v",
        "start_ms": 500,
        "media_id": "2",
    }
    doc = SoundDocument(
        dialogue=[line],
        music={
            "media_id": "3",
            "trim_out_ms": 400,
            "start_ms": 1200,
            "loop": True,
            "volume": 0.3,
            "fade_in_ms": 100,
            "fade_out_ms": 100,
        },
        subtitles=[{"start_ms": 500, "end_ms": 1500, "text": "你好，世界"}],
        burn_subtitles=burn,
    ).model_dump(mode="json")
    snapshot = {
        "version": 3,
        "resolution": "preview",
        "aspect": "16:9",
        "clips": [{"media_id": "1", "trim_in_ms": 0, "trim_out_ms": 2000, "muted": True}],
        "sound": {"document": doc, **font_metadata(settings)},
    }
    out_dir = tmp_path / "render"
    out_dir.mkdir()
    output, meta = VideoRenderer(settings).render(
        snapshot, {"1": video, "2": speech, "3": music}, out_dir
    )
    assert abs(meta["duration_ms"] - 2000) <= 34
    pcm = array(
        "f",
        run_ffmpeg(
            "-i", output, "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"
        ).stdout,
    )

    def peak(start, end):
        return max(abs(v) for v in pcm[round(start * 48000) : round(end * 48000)])

    assert peak(0.1, 0.45) < 0.001
    assert peak(0.55, 0.95) > 0.04
    assert peak(1.05, 1.15) < 0.001
    assert peak(1.35, 1.7) > 0.01
    frames = run_ffmpeg(
        "-i", output, "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
    ).stdout
    assert len(frames) == 60 * 3
    if burn:
        pixels = run_ffmpeg(
            "-ss", "0.8", "-i", output, "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"
        ).stdout
        assert sum(v > 160 for v in pixels) > 100  # visible Chinese glyph pixels over black
        run_ffmpeg("-ss", "0.8", "-i", output, "-frames:v", "1", tmp_path / "subtitle-frame.png")
    else:
        assert max(frames) <= 2


def test_rejects_forged_audio(tmp_path):
    for data in (b"x", b"RIFF0000WAVEgarbage", b"ID3not a recording"):
        file = tmp_path / "bad.wav"
        file.write_bytes(data)
        with pytest.raises(ValueError):
            inspect_audio(file, Settings(_env_file=None))


def test_dialogue_ducks_music_without_shifting_it_and_large_dialogue_is_bounded(tmp_path):
    import math

    from short_drama.service.sound_render import group_dialogue, mix_sound

    video, speech, music = [tmp_path / name for name in ("base.mp4", "speech.wav", "music.wav")]
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x90:r=30:d=2",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-t",
        "2",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        video,
    )
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=600:sample_rate=48000:duration=0.5", speech)
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=200:sample_rate=48000:duration=2", music)
    sources = {"2": speech, "3": music}
    renderer = VideoRenderer(Settings(_env_file=None))
    decoded = []
    for duck in (False, True):
        directory = tmp_path / str(duck)
        directory.mkdir()
        target = directory / "mix.mp4"
        target.write_bytes(video.read_bytes())
        doc = SoundDocument(
            dialogue=[{"id": "a", "text": "voice", "voice": "v", "start_ms": 500, "media_id": "2"}],
            dialogue_volume=2,
            music={
                "media_id": "3",
                "trim_out_ms": 2000,
                "volume": 1,
                "fade_in_ms": 0,
                "fade_out_ms": 0,
                "ducking": duck,
            },
        ).model_dump(mode="json")
        mix_sound(renderer, target, {"document": doc}, sources, directory, 2000, 160, 90)
        decoded.append(
            array(
                "f",
                run_ffmpeg(
                    "-i", target, "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"
                ).stdout,
            )
        )

    def energy(samples, start):
        values = samples[int(start * 48000) : int((start + 0.2) * 48000)]
        return abs(
            sum(
                v
                * complex(
                    math.cos(2 * math.pi * 200 * i / 48000), math.sin(2 * math.pi * 200 * i / 48000)
                )
                for i, v in enumerate(values)
            )
        )

    assert energy(decoded[1], 0.7) < energy(decoded[0], 0.7) * 0.8
    assert 0.9 < energy(decoded[1], 0.1) / energy(decoded[0], 0.1) < 1.1
    directory = tmp_path / "groups"
    directory.mkdir()
    grouped, files = group_dialogue(
        renderer, [{"media_id": "2", "start_ms": 500}] * 17, 0.03, 2, sources, directory
    )
    assert len(grouped) == 2
    assert all(files[line["media_id"]].exists() for line in grouped)
    assert all(line["start_ms"] == 0 for line in grouped)  # each group already has absolute delays
