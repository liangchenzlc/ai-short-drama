"""输出比例按实际视频决定，并在受理后保持稳定。"""

import subprocess

import pytest

from short_drama.core.config import Settings
from short_drama.service.assembly_output import display_dimensions, resolve_output
from short_drama.service.video_render import VideoRenderer, executable


def video(identifier, width, height, kind="shot"):
    return {
        "media_id": identifier,
        "clip_id": identifier,
        "source_type": kind,
        "width": width,
        "height": height,
    }


def test_auto_prefers_first_storyboard_then_import_and_tracks_order():
    square = video("1", 640, 640, "video")
    portrait = video("2", 1080, 1920)
    classic = video("3", 640, 480)
    assert resolve_output("auto", "720p", [square, portrait, classic])["output_width"] == 720
    result = resolve_output("auto", "720p", [square, classic, portrait])
    assert (result["output_width"], result["output_height"]) == (960, 720)
    assert result["output_source"]["media_id"] == "3"
    result = resolve_output("auto", "1080p", [square])
    assert (result["output_width"], result["output_height"]) == (1080, 1080)
    assert resolve_output("auto", "720p", [])["output_width"] == 1280


def test_auto_does_not_silently_skip_unprobed_or_failed_first_video():
    pending = video("1", None, None)
    ready = video("2", 1920, 1080)
    result = resolve_output("auto", "720p", [pending, ready])
    assert result["output_status"] == "preparing" and result["output_width"] is None
    pending["probe_error"] = "decode failed"
    assert resolve_output("auto", "720p", [pending])["output_status"] == "invalid"
    manual = resolve_output("9:16", "720p", [pending])
    assert (manual["output_width"], manual["output_height"]) == (720, 1280)


def test_display_dimensions_honors_rotation_and_non_square_pixels():
    assert display_dimensions(720, 576, "16:15", -90) == (576, 768)
    assert display_dimensions(720, 576, "N/A", 0) == (720, 576)
    result = resolve_output("auto", "preview", [video("1", 1001, 777)])
    assert min(result["output_width"], result["output_height"]) == 360
    assert result["output_width"] % 2 == result["output_height"] % 2 == 0


@pytest.mark.parametrize("rotation", [0, 90])
def test_real_ffmpeg_probe_and_auto_render_display_ratio(tmp_path, rotation):
    renderer = VideoRenderer(Settings(_env_file=None))
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
            "color=c=red:s=320x240:r=30:d=0.2",
            "-vf",
            "setsar=4/3",
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
    if rotation:
        rotated = tmp_path / "rotated.mp4"
        subprocess.run(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-c",
                "copy",
                "-metadata:s:v:0",
                f"rotate={rotation}",
                str(rotated),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        source = rotated
    metadata = renderer.probe(source)
    assert (
        metadata["width"] > metadata["height"]
        if not rotation
        else (metadata["width"] < metadata["height"])
    )
    clip = {
        **video("1", metadata["width"], metadata["height"]),
        "trim_in_ms": 0,
        "trim_out_ms": 200,
        "muted": True,
    }
    dimensions = resolve_output("auto", "preview", [clip])
    snapshot = {"aspect": "auto", "resolution": "preview", "clips": [clip], **dimensions}
    _, output = renderer.render(snapshot, {"1": source}, tmp_path)
    assert (output["width"], output["height"]) == (
        dimensions["output_width"],
        dimensions["output_height"],
    )
