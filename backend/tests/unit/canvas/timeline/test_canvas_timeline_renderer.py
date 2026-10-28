"""渲染进程使用真实 Python 子进程；媒体事实替身仅用于异常与清理边界。"""

import io
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

import short_drama.service.canvas.timeline.timeline_renderer as module
from short_drama.core.config import Settings
from short_drama.schemas.canvas.timeline.timeline import CanvasTimelinePlanRequest
from short_drama.service.canvas.timeline.timeline_compile import compile_timeline_plan
from short_drama.service.canvas.timeline.timeline_native import SourceFacts
from short_drama.service.canvas.timeline.timeline_renderer import (
    BoundedEncoderLog,
    CanvasTimelineRenderer,
    ProbedMedia,
)
from short_drama.service.creation.assembly.rendering.renderer import RenderCancelled


def settings(tmp_path, **overrides):
    return Settings(_env_file=None, render_scratch_root=str(tmp_path / "scratch"), **overrides)


def plan():
    return compile_timeline_plan(
        CanvasTimelinePlanRequest.model_validate(
            {
                "timeline": {
                    "version": 2,
                    "durationMs": 1000,
                    "tracks": [{"id": "v"}],
                    "clips": [
                        {
                            "id": "video",
                            "kind": "video",
                            "trackId": "v",
                            "durationMs": 1000,
                            "directMedia": {"storageKey": "resource:source"},
                        }
                    ],
                },
                "options": {"width": 320, "height": 180},
            }
        )
    )


def test_bounded_log_remembers_early_and_split_font_failures():
    log = BoundedEncoderLog()
    log.write(b"fontselect: fai")
    log.write(b"led to find any fallback\n")
    log.write(b"x" * (128 << 10))
    assert log.font_failure and len(log.tail) == 64 << 10
    assert len(log.detail()) <= 400


@pytest.mark.parametrize("burn, failure", [(True, True), (False, False)])
def test_successful_process_cannot_hide_early_missing_subtitles(tmp_path, burn, failure):
    renderer = CanvasTimelineRenderer(settings(tmp_path))
    script = (
        "import os; os.write(1, b'fontselect: failed to find any fallback\\n'); "
        "os.write(1, b'x' * 131072)"
    )
    if failure:
        with pytest.raises(RuntimeError, match="ffmpeg 时间线渲染失败"):
            renderer._run([sys.executable, "-c", script], tmp_path, burn)
    else:
        renderer._run([sys.executable, "-c", script], tmp_path, burn)


def test_normal_chinese_fallback_is_accepted(tmp_path):
    CanvasTimelineRenderer(settings(tmp_path))._run(
        [
            sys.executable,
            "-c",
            "import os; os.write(1, b'Glyph 0x4E2D not found, selecting one more font\\n')",
        ],
        tmp_path,
        True,
    )


def capture_processes(monkeypatch, *, wait=False):
    original = module.subprocess.Popen
    processes = []

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        if wait:
            process.wait(timeout=5)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    return processes


def assert_processes_closed(processes):
    assert processes and all(process.poll() is not None for process in processes)
    assert all(process.stdout.closed for process in processes)
    assert not any(
        thread.name == "canvas-timeline-encoder-log" and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_cancel_and_timeout_stop_real_process_and_log_reader(tmp_path, monkeypatch, reason):
    processes = capture_processes(monkeypatch)

    def progress(*_):
        if reason == "cancel":
            raise RenderCancelled("用户取消")

    configuration = settings(tmp_path)
    if reason == "timeout":
        configuration = configuration.model_copy(update={"render_timeout_seconds": 0.02})
    renderer = CanvasTimelineRenderer(configuration, progress)
    expected = RenderCancelled if reason == "cancel" else RuntimeError
    with pytest.raises(expected, match="用户取消" if reason == "cancel" else "渲染超时"):
        renderer._run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, False)
    assert_processes_closed(processes)


def test_reader_failure_is_reported_and_stops_real_process(tmp_path, monkeypatch):
    processes = capture_processes(monkeypatch)

    def fail_log(_self, _data):
        raise OSError("log stream failed")

    monkeypatch.setattr(BoundedEncoderLog, "write", fail_log)
    with pytest.raises(RuntimeError, match="编码日志读取失败") as failure:
        CanvasTimelineRenderer(settings(tmp_path))._run(
            [sys.executable, "-c", "import os; os.write(1, b'x' * 4096)"], tmp_path, False
        )
    assert isinstance(failure.value.__cause__, OSError)
    assert_processes_closed(processes)


def test_fast_success_still_checks_final_scratch_usage(tmp_path, monkeypatch):
    processes = capture_processes(monkeypatch, wait=True)
    renderer = CanvasTimelineRenderer(settings(tmp_path, render_max_scratch_bytes=1024))
    with pytest.raises(RuntimeError, match="临时空间达到上限"):
        renderer._run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('output').write_bytes(b'x'*1025)",
            ],
            tmp_path,
            False,
        )
    assert_processes_closed(processes)


def test_spawn_failure_has_render_context(tmp_path, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("cannot execute encoder")

    monkeypatch.setattr(module.subprocess, "Popen", unavailable)
    with pytest.raises(RuntimeError, match="无法启动时间线编码器") as failure:
        CanvasTimelineRenderer(settings(tmp_path))._run(["missing"], tmp_path, False)
    assert isinstance(failure.value.__cause__, OSError)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"streams": None},
        {"streams": ["invalid"]},
        {"streams": [], "format": []},
        {"streams": [{"codec_type": "video", "width": "unknown"}]},
        {"streams": [], "format": {"duration": "1e308"}},
    ],
)
def test_invalid_probe_structure_has_media_context(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(module, "executable", lambda _: sys.executable)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload).encode()),
    )
    with pytest.raises(RuntimeError, match="媒体音轨探测失败"):
        CanvasTimelineRenderer(settings(tmp_path)).probe(tmp_path / "source")


def stub_media_boundaries(renderer, monkeypatch, output=None):
    monkeypatch.setattr(module, "executable", lambda _: sys.executable)
    source = ProbedMedia(SourceFacts(True, True, 1000), 320, 180)
    monkeypatch.setattr(renderer, "probe", lambda path: output or source)

    def write_output(_args, directory, _burn):
        (directory / "render-output.mp4").write_bytes(b"test output, not encoded media")

    monkeypatch.setattr(renderer, "_run", write_output)


@pytest.mark.parametrize("failure_stage", ["open", "read", "source_limit", "callback", "archive"])
def test_render_releases_source_and_directory_on_failure(tmp_path, monkeypatch, failure_stage):
    renderer = CanvasTimelineRenderer(settings(tmp_path, render_max_source_bytes=1024))
    stub_media_boundaries(renderer, monkeypatch)
    opened = []

    class BrokenStream(io.BytesIO):
        def read(self, *args):
            if failure_stage == "read":
                raise OSError("source interrupted")
            return super().read(*args)

    @contextmanager
    def open_source(_identifier):
        if failure_stage == "open":
            raise OSError("missing source")
        stream = BrokenStream(b"x" * (1025 if failure_stage == "source_limit" else 10))
        opened.append(stream)
        with stream:
            yield stream

    def progress(label, _percent):
        if failure_stage == "callback" and opened and label == "准备媒体…":
            raise RenderCancelled("用户取消")

    renderer.progress = progress
    expected = RenderCancelled if failure_stage == "callback" else RuntimeError
    with pytest.raises(expected):
        with renderer.render(plan(), open_source) as rendered:
            assert failure_stage == "archive" and rendered.path.is_file()
            raise RuntimeError("归档失败")
    assert all(stream.closed for stream in opened)
    assert not list(Path(renderer.settings.render_scratch_root).iterdir())


@pytest.mark.parametrize(
    "output, message",
    [
        (ProbedMedia(SourceFacts(False, True, 1000), 320, 180), "缺少音视频轨"),
        (ProbedMedia(SourceFacts(True, True, 2000), 320, 180), "时长无效"),
        (ProbedMedia(SourceFacts(True, True, 1000), 640, 360), "尺寸无效"),
    ],
)
def test_invalid_output_is_rejected_and_cleaned(tmp_path, monkeypatch, output, message):
    renderer = CanvasTimelineRenderer(settings(tmp_path))
    stub_media_boundaries(renderer, monkeypatch, output)
    with pytest.raises(RuntimeError, match=message):
        with renderer.render(plan(), lambda _: io.BytesIO(b"source")):
            pytest.fail("invalid output must not be returned")
    assert not list(Path(renderer.settings.render_scratch_root).iterdir())


def test_render_does_not_mutate_original_plan_and_cleans_success(tmp_path, monkeypatch):
    renderer = CanvasTimelineRenderer(settings(tmp_path))
    stub_media_boundaries(renderer, monkeypatch)
    compiled = plan()
    previous = compiled.model_dump()
    with renderer.render(compiled, lambda _: io.BytesIO(b"source")) as rendered:
        directory = rendered.path.parent
        assert rendered.size > 0 and directory.is_dir()
        assert compiled.model_dump() == previous
    assert not directory.exists()
