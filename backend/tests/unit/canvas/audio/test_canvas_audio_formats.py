"""画布原音频选项的真实编码字节；不扩大标准声音编辑器的上传合同。"""

import subprocess
from types import SimpleNamespace

import pytest

from short_drama.core.config import Settings
from short_drama.service.creation.assembly.rendering.renderer import executable
from short_drama.service.media.processing.audio import inspect_audio

CODECS = {
    "mp3": ["-c:a", "libmp3lame"],
    "wav": ["-c:a", "pcm_s16le"],
    "opus": ["-c:a", "libopus", "-f", "opus"],
    "aac": ["-c:a", "aac", "-f", "adts"],
    "flac": ["-c:a", "flac"],
    "pcm": ["-c:a", "pcm_s16le", "-f", "s16le"],
}
MIMES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "pcm": "audio/pcm",
}


def encoded_audio(tmp_path, format):
    target = tmp_path / ("encoded." + format)
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=24000",
            "-t",
            "1",
            "-ac",
            "1",
            *CODECS[format],
            str(target),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return target


@pytest.mark.parametrize("format", CODECS)
def test_canvas_generated_audio_preserves_the_real_source_formats(tmp_path, format):
    file = encoded_audio(tmp_path, format)
    from short_drama.service.generations.results.archive import GenerationArchive

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _id):
            return SimpleNamespace(
                request_data={
                    "canvas_request": {},
                    "canvas_parameters": {"mode": "audio", "format": format},
                    "source": {"scene": "canvas_node"},
                }
            )

    archive = GenerationArchive(Session, Settings(_env_file=None), None, None)
    result = archive._describe(1, 2, {"asset_id": "123", "media_type": "audio"}, file.read_bytes())
    assert result["mime"] == MIMES[format] and result["ext"] == format
    if format == "pcm":
        assert result.get("duration_ms") is None
        assert result["byte_size"] == file.stat().st_size
    else:
        assert 900 <= result["duration_ms"] <= 1500


@pytest.mark.parametrize("format", ["opus", "aac", "flac", "pcm"])
def test_standard_sound_upload_keeps_its_existing_supported_formats(tmp_path, format):
    file = encoded_audio(tmp_path, format)
    if format == "aac":
        # 旧检查把AAC的0xff头归为MP3；本片只在画布入口正确辨别AAC。
        assert inspect_audio(file, Settings(_env_file=None))["mime"] == "audio/mpeg"
    else:
        with pytest.raises(ValueError):
            inspect_audio(file, Settings(_env_file=None))
