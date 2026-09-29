"""Decode actual files to compare browser proxy frames against both render modes."""

import subprocess
from array import array

import pytest

from short_drama.core.config import Settings
from short_drama.service.video_render import VideoRenderer, executable


def decode_colors(path):
    result = subprocess.run(
        [executable("ffmpeg"), "-v", "error", "-i", str(path), "-vf", "scale=1:1",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        check=True, capture_output=True, timeout=30,
    )
    return list(zip(*[iter(result.stdout)] * 3, strict=True))


@pytest.mark.parametrize("rate", ["24", "30000/1001", "vfr"])
def test_trimmed_export_matches_proxy_frames_at_non_native_boundaries(tmp_path, rate):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
         f"nullsrc=s=160x90:r={'30' if rate == 'vfr' else rate}:d=2,format=rgb24,"
         "geq=r='mod(N*43,256)':g='mod(N*97,256)':b='mod(N*17,256)'",
         *(["-vf", "select='not(eq(mod(n,3),1))'", "-fps_mode", "vfr"] if rate == "vfr" else []),
         "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True, timeout=30,
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
            {"version": 2, "resolution": resolution, "aspect": "16:9", "fps": 30,
             "clips": [{"media_id": "1", "trim_in_ms": round(start * 1000 / 30),
                        "trim_out_ms": round(end * 1000 / 30), "muted": False}
                       for start, end in cuts]},
            {"1": source}, directory,
        )
        actual = decode_colors(output)
        assert len(actual) == len(expected) == 15
        assert abs(metadata["duration_ms"] - 500) <= 34
        for index, (rendered, preview) in enumerate(zip(actual, expected, strict=True)):
            assert max(abs(a - b) for a, b in zip(rendered, preview, strict=True)) <= 8, (
                resolution, rate, index, rendered, preview
            )


def test_trimmed_audio_pulse_stays_synchronized_and_muted_segments_stay_silent(tmp_path):
    source = tmp_path / "pulse.mp4"
    subprocess.run(
        [executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=160x90:r=30:d=1,drawbox=color=lime:t=fill:enable='between(t,0.5,0.74)'",
         "-f", "lavfi", "-i", "aevalsrc=0.3*sin(2*PI*600*t)*between(t\\,0.5\\,0.75):s=44100:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
        check=True, capture_output=True, timeout=30,
    )
    renderer = VideoRenderer(Settings())
    output, metadata = renderer.render(
        {"version": 2, "resolution": "preview", "aspect": "16:9", "fps": 30,
         "clips": [{"media_id": "1", "trim_in_ms": 400, "trim_out_ms": 900, "muted": muted}
                   for muted in (False, True)]}, {"1": source}, tmp_path,
    )
    decoded = subprocess.run(
        [executable("ffmpeg"), "-v", "error", "-i", str(output), "-map", "0:a:0",
         "-t", "1", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
        check=True, capture_output=True, timeout=30,
    )
    samples = array("f", decoded.stdout)
    assert len(samples) == 48000
    assert abs(metadata["duration_ms"] - 1000) <= 34

    def energy(start, end):
        window = samples[round(start * 48000):round(end * 48000)]
        return sum(sample * sample for sample in window) / len(window)

    assert energy(.02, .07) < .00001
    assert energy(.13, .30) > .005
    assert energy(.4, .48) < .00001
    assert energy(.52, .98) < .00001
    colors = decode_colors(output)
    assert len(colors) == 30
    assert colors[2][1] < 10 and colors[3][1] > 200
    assert colors[17][1] < 10 and colors[18][1] > 200
