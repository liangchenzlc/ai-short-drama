import base64
import copy
import json
import math
import time
from array import array
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError
from test_sound_production import run_ffmpeg

from short_drama.ai.adapters import build_submission, parse_result, validate_request
from short_drama.ai.gateway import GenerationGateway
from short_drama.ai.types import GenerationError
from short_drama.core.config import Settings
from short_drama.schemas.episode_sound import SoundDocument
from short_drama.schemas.native_voice import NativeDialogueDocument
from short_drama.service.native_video_media import inspect_native_video
from short_drama.service.native_voice_service import native_context, native_prompt
from short_drama.service.sound_render import mix_sound
from short_drama.service.video_render import VideoRenderer

DESIGN = "dashscope_voice_design.v1"
SNAPSHOT = {
    "service_type": "audio",
    "model_key": "cosyvoice-v3.5-flash",
    "base_url": "https://dashscope.aliyuncs.com/api/v1",
}
REQUEST = {
    "input": {
        "voice_prompt": "沉稳自然的青年男性",
        "preview_text": "清晨的风吹过窗前，今天又是新的开始。",
    },
    "parameters": {},
}


def test_voice_design_uses_preview_without_second_tts_submission():
    url, _, body = build_submission(SNAPSHOT, REQUEST, DESIGN)
    assert url.endswith("/services/audio/tts/customization")
    assert body["input"]["action"] == "create_voice"
    assert "url" not in body["input"]
    assert body["input"]["target_model"] == "cosyvoice-v3.5-flash"
    assert body["parameters"] == {"sample_rate": 24000, "response_format": "wav"}
    response = {
        "request_id": "req-1",
        "output": {
            "voice_id": "voice-1",
            "target_model": "cosyvoice-v3.5-flash",
            "preview_audio": {
                "data": base64.b64encode(b"preview").decode(),
                "response_format": "wav",
            },
        },
        "usage": {"count": 1},
    }
    gateway = GenerationGateway(Settings(_env_file=None))
    calls = []

    def send(*args, **kwargs):
        calls.append(kwargs)
        return 200, {}, json.dumps(response).encode()

    gateway.transport.request = send
    result = gateway.submit(SNAPSHOT, REQUEST, "fixture", adapter=DESIGN)
    assert result.voice["voice_id"] == "voice-1"
    assert result.outputs[0]["base64"] == response["output"]["preview_audio"]["data"]
    assert len(calls) == 1


@pytest.mark.parametrize("output", [None, {}, {"voice_id": "v"}])
def test_malformed_design_result_never_allows_automatic_paid_retry(output):
    with pytest.raises(GenerationError) as caught:
        parse_result({"request_id": "req", "output": output}, DESIGN, submitted=True)
    assert caught.value.accepted_unknown and not caught.value.retryable


def test_disabled_native_does_not_touch_unmigrated_database():
    assert (
        native_context(None, None, settings=Settings(_env_file=None, native_video_enabled=False))
        is None
    )


def test_dialogue_max_two_speakers_and_explicit_voice_mapping():
    lines = [{"character_id": str(i), "text": f"line {i}"} for i in (1, 2, 1)]
    doc = NativeDialogueDocument(lines=lines, reviewed=True).model_dump(mode="json")
    prompt = native_prompt(
        {
            **doc,
            "voices": [{"character_id": "1", "name": "甲"}, {"character_id": "2", "name": "乙"}],
        }
    )
    assert "甲" in prompt and "音频1" in prompt and "音频2" in prompt
    assert prompt.index("台词：line 1") < prompt.index("台词：line 2")
    with pytest.raises(ValidationError):
        NativeDialogueDocument(lines=lines + [{"character_id": "3", "text": "third"}])


def test_image_and_two_audio_multipart_mapping_and_no_unsupported_fallback(tmp_path):
    gateway = GenerationGateway(Settings(_env_file=None))
    snap = {
        "service_type": "video",
        "model_key": "seedance-2.0-mini",
        "base_url": "https://api.modelhub.cc",
    }
    request = {
        "input": {
            "prompt": "两人轮流说话",
            "reference_urls": ["https://reference.invalid/image"],
            "audio_reference_urls": ["https://reference.invalid/a", "https://reference.invalid/b"],
        },
        "parameters": {"duration_ms": 4000, "resolution": "480p", "generate_audio": True},
    }
    _, _, body = build_submission(snap, request, "modelhub_video.v1")
    picture = BytesIO()
    Image.new("RGB", (4, 4)).save(picture, "PNG")
    audio = []
    for frequency in (300, 600):
        path = tmp_path / f"{frequency}.wav"
        run_ffmpeg("-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=3.1", path)
        audio.append(path.read_bytes())
    result = gateway._image_edit_body(
        body,
        time.monotonic() + 60,
        lambda *args: picture.getvalue(),
        modelhub=True,
        audio_reference_loader=lambda i, *args: audio[i],
    )
    fields = dict(result.fields)
    assert fields["audio_file_1"][1] == audio[0]
    assert fields["audio_file_2"][1] == audio[1]
    assert fields["generate_audio"] == "true"
    invalid = {**snap, "model_key": "unknown"}
    with pytest.raises(GenerationError):
        validate_request(invalid, request)
    with pytest.raises(GenerationError):
        gateway._image_edit_body(
            body, time.monotonic() + 60, lambda *args: picture.getvalue(), modelhub=True
        )


@pytest.mark.parametrize("audio", ["absent", "silent", "audible"])
def test_native_video_full_decode_and_audible_track_gate(tmp_path, audio):
    path = tmp_path / "video.mp4"
    args = ["-f", "lavfi", "-i", "color=s=160x90:r=30:d=1"]
    if audio != "absent":
        args += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo" if audio == "silent" else "sine=frequency=600:duration=1",
            "-c:a",
            "aac",
        ]
    run_ffmpeg(*args, "-t", "1", "-c:v", "libx264", path)
    quality = inspect_native_video(path.read_bytes(), Settings(_env_file=None), True)[
        "native_quality"
    ]
    assert quality["technical_pass"] == (audio == "audible")
    assert quality["voice_fidelity"] == "requires_human_review"
    assert inspect_native_video(path.read_bytes(), Settings(_env_file=None), False)[
        "native_quality"
    ]["technical_pass"]


def test_native_mix_retains_original_speech_and_only_ducks_music_in_intervals(tmp_path):
    video, music = tmp_path / "source.mp4", tmp_path / "music.wav"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=s=160x90:r=30:d=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=600:duration=2",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-t",
        "2",
        video,
    )
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=200:duration=2", music)
    doc = SoundDocument(
        music={
            "media_id": "2",
            "trim_out_ms": 2000,
            "volume": 1,
            "fade_in_ms": 0,
            "fade_out_ms": 0,
        },
        native_ducking=[{"start_ms": 500, "end_ms": 1200, "text": "dialogue"}],
        burn_subtitles=False,
    ).model_dump(mode="json")
    samples = []
    for duck in (False, True):
        directory = tmp_path / str(duck)
        directory.mkdir()
        target = directory / "mix.mp4"
        target.write_bytes(video.read_bytes())
        current = copy.deepcopy(doc)
        current["music"]["ducking"] = duck
        mix_sound(
            VideoRenderer(Settings(_env_file=None)),
            target,
            {"mode": "native", "document": current},
            {"2": music},
            directory,
            2000,
            160,
            90,
        )
        samples.append(
            array(
                "f",
                run_ffmpeg(
                    "-i", target, "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"
                ).stdout,
            )
        )

    def energy(values, start, frequency):
        chunk = values[int(start * 48000) : int((start + 0.2) * 48000)]
        return abs(
            sum(
                v
                * complex(
                    math.cos(2 * math.pi * frequency * i / 48000),
                    math.sin(2 * math.pi * frequency * i / 48000),
                )
                for i, v in enumerate(chunk)
            )
        )

    assert 0.20 < energy(samples[1], 0.7, 200) / energy(samples[0], 0.7, 200) < 0.30
    assert 0.9 < energy(samples[1], 0.1, 200) / energy(samples[0], 0.1, 200) < 1.1
    assert 0.9 < energy(samples[1], 0.7, 600) / energy(samples[0], 0.7, 600) < 1.1
