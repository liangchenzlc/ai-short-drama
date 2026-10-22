"""显式启用的固定源真实深度模型；不调用收费供应商，不使用组件替身。"""

import hashlib
import os
from pathlib import Path

import pytest

from short_drama.core.config import Settings
from short_drama.service.canvas_depth_execute import CanvasDepthCaptureExecutor
from short_drama.service.canvas_timeline_renderer import CanvasTimelineRenderer
from tests.integration.canvas.timeline.test_canvas_timeline_ffmpeg import ffmpeg

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_DEPTH_MODEL") != "1",
        reason="Explicitly enable actual fixed Video Depth Anything Small inference",
    ),
]


def test_fixed_depth_model_executes_real_video_and_cleans_temporary_files(tmp_path):
    settings = Settings(_env_file=None, render_scratch_root=str(tmp_path / "scratch"))
    assert all(
        (
            settings.beeftv_depth_python,
            settings.beeftv_depth_tool_dir,
            settings.beeftv_depth_runtime,
        )
    ), "Configure verified independent fixed source runtime"
    source = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=64x48:rate=5:duration=0.4",
        "-pix_fmt",
        "yuv420p",
        source,
    )
    progress = []
    with source.open("rb") as stream:
        with CanvasDepthCaptureExecutor(settings, lambda *value: progress.append(value)).capture(
            stream, {"mime_type": "video/mp4", "duration_ms": 400}
        ) as result:
            data = result.path.read_bytes()
            probe = CanvasTimelineRenderer(settings).probe(result.path)
            assert (probe.width, probe.height, probe.facts.duration_ms) == (1920, 1080, 400)
            assert result.size == len(data) > 0 and result.device in {"cpu", "cuda"}
            assert result.processing_ms > 0 and data != source.read_bytes()
            path = tmp_path / "actual-depth.mp4"
            path.write_bytes(data)
    assert list(Path(settings.render_scratch_root).glob("beeftv-depth-*")) == []
    checkpoint = Path(settings.beeftv_depth_runtime) / "checkpoints/video_depth_anything_vits.pth"
    assert checkpoint.is_file() and hashlib.sha256(checkpoint.read_bytes()).hexdigest() == (
        "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609"
    )
    assert any("分析视频" in value[0] for value in progress)
