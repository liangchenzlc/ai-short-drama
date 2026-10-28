"""深度独立组件失败与取消边界；不以替身证明实际模型推理。"""

from pathlib import Path

import pytest

from short_drama.core.config import Settings
from short_drama.service.canvas.tools.depth.depth_execute import (
    CanvasDepthCaptureExecutor,
    DepthComponentFailure,
    supported_platform,
)
from short_drama.service.canvas.tools.depth.depth_runtime import CanvasDepthRuntime
from short_drama.service.creation.assembly.rendering.renderer import RenderCancelled


@pytest.mark.parametrize(
    "system,machine,result",
    [
        ("Windows", "AMD64", "windows-amd64"),
        ("Darwin", "arm64", "darwin-arm64"),
        ("Linux", "x86_64", None),
        ("Windows", "arm64", None),
        ("Darwin", "x86_64", None),
    ],
)
def test_only_source_platforms_are_supported(system, machine, result):
    assert supported_platform(system, machine) == result


def test_partial_manual_component_config_is_rejected(tmp_path):
    settings = Settings(_env_file=None, beeftv_depth_python=str(tmp_path / "python"))
    with pytest.raises(RuntimeError, match="同时配置"):
        CanvasDepthRuntime(settings, lambda *_: None).resolve("windows-amd64", "cpu")


def test_missing_windows_public_key_never_fetches_component(tmp_path):
    settings = Settings(_env_file=None, canvas_depth_runtime_root=str(tmp_path))
    with pytest.raises(RuntimeError, match="可信发布公钥"):
        CanvasDepthRuntime(settings, lambda *_: None).resolve("windows-amd64", "cpu")
    assert not list(tmp_path.glob("downloads/*"))


@pytest.mark.parametrize("code,retry", [(42, True), (1, False), (2, False)])
def test_only_cuda_device_exit_is_eligible_for_cpu_fallback(code, retry):
    error = DepthComponentFailure(code, "component failed")
    assert error.cuda_device_failure is retry


def test_cancel_during_component_prepare_does_not_enter_inference(tmp_path):
    settings = Settings(_env_file=None, render_scratch_root=str(tmp_path / "scratch"))

    def stop(*_):
        raise RenderCancelled()

    with pytest.raises(RenderCancelled):
        with CanvasDepthCaptureExecutor(settings, stop).capture(
            None, {"mime_type": "video/mp4", "duration_ms": 1000}
        ):
            pytest.fail("cancelled execution returned a result")
    assert not list(Path(settings.render_scratch_root).glob("beeftv-depth-*"))
