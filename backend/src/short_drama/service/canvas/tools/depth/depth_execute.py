"""固定源 Video Depth Anything Small 子进程；真实字节与输出验证后才可归档。"""

import os
import platform
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import BinaryIO

from short_drama.core.config import Settings
from short_drama.service.canvas.timeline.timeline_renderer import CanvasTimelineRenderer
from short_drama.service.canvas.tools.depth.depth_runtime import (
    CanvasDepthRuntime,
    DepthInstallation,
    Progress,
)
from short_drama.service.creation.assembly.rendering.renderer import executable


@dataclass(frozen=True)
class CapturedDepth:
    path: Path
    size: int
    width: int
    height: int
    duration_ms: int
    device: str
    fallback_reason: str
    processing_ms: int


class DepthComponentFailure(RuntimeError):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.cuda_device_failure = exit_code == 42


def supported_platform(system: str | None = None, machine: str | None = None) -> str | None:
    system, machine = system or platform.system(), machine or platform.machine()
    if system == "Windows" and machine.lower() in {"amd64", "x86_64"}:
        return "windows-amd64"
    if system == "Darwin" and machine.lower() == "arm64":
        return "darwin-arm64"
    return None


class DepthLog:
    def __init__(self) -> None:
        self.tail = b""
        self.scan_tail = b""
        self.stage, self.progress = "加载深度模型", 15

    def write(self, block: bytes) -> None:
        scanned = self.scan_tail + block
        if b"[2/3]" in scanned:
            self.stage, self.progress = "分析视频", 35
        if b"[3/3]" in scanned:
            self.stage, self.progress = "生成结果视频", 82
        self.scan_tail = scanned[-16:]
        self.tail = (self.tail + block)[-65536:]

    def detail(self) -> str:
        return self.tail[-800:].decode("utf-8", errors="replace").strip()


class CanvasDepthCaptureExecutor:
    def __init__(self, settings: Settings, progress: Progress) -> None:
        self.settings, self.progress = settings, progress
        self.runtime = CanvasDepthRuntime(settings, progress)
        self.prober = CanvasTimelineRenderer(settings, progress)

    def _environment(self, installation: DepthInstallation, host: str) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            PYTORCH_ENABLE_MPS_FALLBACK="1",
            BEEFTV_VDA_SOURCE=str(installation.tool_dir.parent / "vda"),
            PYTHONIOENCODING="utf-8",
            PYTHONUNBUFFERED="1",
        )
        path_entries = (
            [str(installation.tool_dir.parent / "bin")] if host == "windows-amd64" else []
        )
        # 固定组件使用PATH查找ffmpeg/ffprobe；沿用服务端已验证的显式配置。
        path_entries.extend(
            str(Path(executable(value)).resolve().parent)
            for value in (self.settings.render_ffmpeg_path, self.settings.render_ffprobe_path)
        )
        environment["PATH"] = os.pathsep.join([*path_entries, environment.get("PATH", "")])
        return environment

    def _run(
        self,
        installation: DepthInstallation,
        host: str,
        arguments: list[str],
        scratch: Path,
        *,
        timeout: int | None = None,
    ) -> None:
        log = DepthLog()
        errors: list[Exception] = []
        try:
            process = subprocess.Popen(
                [str(installation.python), *arguments],
                cwd=installation.tool_dir,
                env=self._environment(installation, host),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            raise RuntimeError("无法启动独立深度处理组件，请检查 Python 及工具目录") from error
        with process:
            assert process.stdout is not None

            def collect() -> None:
                try:
                    while block := process.stdout.read1(4096):
                        log.write(block)
                except Exception as error:
                    errors.append(error)

            reader = Thread(target=collect, name="canvas-depth-component-log", daemon=True)
            reader.start()
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if errors:
                        raise RuntimeError("读取深度处理组件进度失败") from errors[0]
                    self.progress(log.stage, log.progress)
                    if time.monotonic() - started > (
                        timeout or self.settings.render_timeout_seconds
                    ):
                        raise RuntimeError("深度处理超时，请缩短视频或检查独立组件后恢复")
                    self._check_scratch(scratch)
                    time.sleep(0.1)
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        # 只终止此调用创建的进程树，避免遗留独立组件的编码子进程。
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                reader.join(5)
                if reader.is_alive():
                    raise RuntimeError("深度组件进度读取未结束")
            if errors:
                raise RuntimeError("读取深度处理组件进度失败") from errors[0]
            self._check_scratch(scratch)
            if process.returncode:
                raise DepthComponentFailure(
                    process.returncode, log.detail() or "深度处理组件未返回失败诊断"
                )

    def _check_scratch(self, scratch: Path) -> None:
        used = sum(path.stat().st_size for path in scratch.rglob("*") if path.is_file())
        if used > self.settings.render_max_scratch_bytes:
            raise RuntimeError("深度处理临时空间达到上限")

    def _device(self, host: str, scratch: Path) -> tuple[DepthInstallation, str, str]:
        if host == "darwin-arm64":
            return self.runtime.resolve(host, "mps"), "mps", ""
        self.progress("检查 NVIDIA CUDA 设备", 1)
        candidate = False
        try:
            probe = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            candidate = probe.returncode == 0 and bool(probe.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            candidate = False
        reason = "未检测到可用的 NVIDIA CUDA 设备"
        if candidate:
            installation = self.runtime.resolve(host, "cuda")
            try:
                self.progress("验证 CUDA 深度模型", 2)
                self._run(
                    installation,
                    host,
                    [
                        "-m",
                        "depth_capture.probe",
                        "--device",
                        "cuda",
                        "--runtime-dir",
                        str(installation.model_runtime),
                        "--work-dir",
                        str(scratch / "probe"),
                    ],
                    scratch,
                )
                return installation, "cuda", ""
            except DepthComponentFailure as error:
                if not error.cuda_device_failure:
                    raise
                reason = str(error)
        return self.runtime.resolve(host, "cpu"), "cpu", reason

    def _infer(
        self,
        installation: DepthInstallation,
        host: str,
        device: str,
        source: Path,
        output: Path,
        scratch: Path,
    ) -> None:
        self.progress("使用 CPU 处理，可能耗时较长" if device == "cpu" else "加载深度模型", 15)
        self._run(
            installation,
            host,
            [
                "-m",
                "depth_capture",
                str(source),
                "--output-dir",
                str(output),
                "--runtime-dir",
                str(installation.model_runtime),
                "--device",
                device,
                "--input-size",
                "280",
                "--max-resolution",
                "960",
                "--output-resolution",
                "1920x1080",
                "--max-seconds",
                "15",
                "--low-percentile",
                "2",
                "--high-percentile",
                "98",
                "--gamma",
                "1.25",
            ],
            scratch,
        )

    @contextmanager
    def capture(self, source: BinaryIO, snapshot: dict) -> Iterator[CapturedDepth]:
        self.progress("检查深度处理组件", 1)
        host = supported_platform()
        if host is None:
            raise RuntimeError("当前版本仅支持 Apple Silicon Mac 和 Windows x64")
        if not snapshot["mime_type"].startswith("video/"):
            raise RuntimeError("深度动作捕捉仅支持视频资源")
        if (snapshot.get("duration_ms") or 0) > 15100:
            raise RuntimeError("视频超过 15 秒，请先使用视频剪辑缩短")
        started = time.monotonic()
        root = Path(self.settings.render_scratch_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="beeftv-depth-", dir=root) as temporary:
            scratch = Path(temporary).resolve()
            if scratch == root or not scratch.is_relative_to(root):
                raise RuntimeError("深度处理临时目录无效")
            path = scratch / "input.mp4"
            with path.open("xb") as output:
                size = 0
                while block := source.read(1024**2):
                    size += len(block)
                    self.progress("准备视频", 8)
                    if size > min(
                        self.settings.render_max_source_bytes,
                        self.settings.render_max_scratch_bytes,
                    ):
                        raise RuntimeError("深度来源超过处理空间上限")
                    output.write(block)
            facts = self.prober.probe(path).facts
            if not facts.has_video or facts.duration_ms <= 0:
                raise RuntimeError("输入文件缺少有效的视频帧或时间信息")
            if facts.duration_ms > 15100:
                raise RuntimeError("视频超过 15 秒，请先使用视频剪辑缩短")
            installation, device, reason = self._device(host, scratch)
            output = scratch / "output"
            output.mkdir()
            try:
                self._infer(installation, host, device, path, output, scratch)
            except DepthComponentFailure as error:
                if device != "cuda" or not error.cuda_device_failure:
                    raise
                self.progress("CUDA 设备故障，改用 CPU（可能耗时较长）", 15)
                device, reason = "cpu", str(error)
                installation = self.runtime.resolve(host, device)
                # 只清理由本次可信组件生成的输出，源文件及其他任务保持独立。
                if not output.resolve().is_relative_to(scratch):
                    raise RuntimeError("无法安全清理未完成的 CUDA 输出") from error
                import shutil

                shutil.rmtree(output)
                output.mkdir()
                self._infer(installation, host, device, path, output, scratch)
            matches = list(output.glob("*_depth_preview.mp4"))
            if len(matches) != 1 or matches[0].is_symlink():
                raise RuntimeError("深度处理组件没有生成唯一有效的视频文件")
            target = matches[0]
            measured = self.prober.probe(target)
            if (measured.width, measured.height) != (1920, 1080) or not measured.facts.has_video:
                raise RuntimeError("深度预览分辨率不符合标准配置")
            if (
                measured.facts.duration_ms <= 0
                or abs(measured.facts.duration_ms - facts.duration_ms) > 250
            ):
                raise RuntimeError("深度预览时长与输入不匹配")
            if target.stat().st_size <= 0:
                raise RuntimeError("生成的视频文件为空")
            self.progress("保存到素材库", 92)
            yield CapturedDepth(
                target,
                target.stat().st_size,
                1920,
                1080,
                measured.facts.duration_ms,
                device,
                reason[:800],
                int((time.monotonic() - started) * 1000),
            )
