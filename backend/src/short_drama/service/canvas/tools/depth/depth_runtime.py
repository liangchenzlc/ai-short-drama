"""固定源独立深度组件安装；Windows 清单必须以部署公钥验签。"""

import base64
import hashlib
import json
import os
import shutil
import stat
import time
import zipfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from short_drama.core.config import Settings
from short_drama.service.creation.assembly.rendering.renderer import RenderCancelled

Progress = Callable[[str, int], None]
Positive = Annotated[int, Field(strict=True, gt=0)]
WINDOWS_MANIFEST = (
    "https://github.com/glanderness/BeefTV/releases/download/"
    "depth-runtime-v2/depth-runtime-manifest.v2.json"
)
MAC_MANIFEST = (
    "https://github.com/glanderness/BeefTV/releases/download/v1.5.5/depth-runtime-manifest.json"
)


class DepthPart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    urls: list[str] = Field(min_length=1)
    size: Positive
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("urls")
    @classmethod
    def valid_urls(cls, urls: list[str]) -> list[str]:
        for url in urls:
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
                raise ValueError("深度组件下载地址无效")
        return urls


class DepthArtifact(DepthPart):
    urls: list[str] = Field(default_factory=list)
    parts: list[DepthPart] = Field(default_factory=list, max_length=16)
    files: Annotated[int, Field(strict=True, ge=0, le=50000)] = 0
    expanded_size: Annotated[int, Field(strict=True, ge=0)] = Field(default=0, alias="expandedSize")


class DepthManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1, 2]
    runtime: DepthArtifact | None = None
    runtimes: dict[str, DepthArtifact] = Field(default_factory=dict)
    model: DepthArtifact


@dataclass(frozen=True)
class DepthInstallation:
    python: Path
    tool_dir: Path
    model_runtime: Path


def verify_manifest(raw: bytes, public_key: bytes) -> DepthManifest:
    try:
        if len(raw) > 1024**2 or len(public_key) != 32:
            raise ValueError("缺少可信的深度组件公钥或清单过大")
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise ValueError("深度组件签名封装无效")
        payload = base64.b64decode(envelope["payload"].strip(), validate=True)
        signature = base64.b64decode(envelope["signature"].strip(), validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
        manifest = DepthManifest.model_validate_json(payload)
        if manifest.version != 2 or not manifest.runtimes:
            raise ValueError("深度组件清单版本或运行包为空")
        return manifest
    except (InvalidSignature, ValueError, TypeError, KeyError, AttributeError) as error:
        raise RuntimeError("深度组件清单签名或内容验证失败") from error


def runtime_layout(root: Path, platform: str, variant: str) -> tuple[Path, Path]:
    base = root / "runtimes/depth/v1" / platform
    if platform == "windows-amd64" and variant in {"cpu", "cuda"}:
        return base / variant, root / "downloads" / f"depth-runtime-v1-{platform}-{variant}.zip"
    if platform == "darwin-arm64" and variant == "mps":
        return base, root / "downloads/depth-runtime-v1-darwin-arm64.zip"
    raise RuntimeError(f"不支持的深度运行包平台或设备: {platform}/{variant}")


def verify_artifact(path: Path, artifact: DepthPart, progress: Progress) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != artifact.size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while block := source.read(1024**2):
                progress("校验深度处理组件", 2)
                digest.update(block)
        return digest.hexdigest() == artifact.sha256.lower()
    except OSError:
        return False


def extract_runtime(
    archive_path: Path, destination: Path, artifact: DepthArtifact, maximum: int, progress: Progress
) -> None:
    destination = destination.resolve()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            expanded = sum(entry.file_size for entry in entries)
            if (
                len(entries) > 50000
                or expanded > maximum
                or (artifact.files and len(entries) != artifact.files)
                or (artifact.expanded_size and expanded != artifact.expanded_size)
            ):
                raise RuntimeError("深度组件解压体积或文件数与发布清单不一致")
            targets = []
            seen: set[Path] = set()
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                target = (destination / name).resolve()
                mode = entry.external_attr >> 16
                if (
                    ":" in name
                    or name.startswith("/")
                    or target == destination
                    or not target.is_relative_to(destination)
                    or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})
                    or target in seen
                ):
                    raise RuntimeError("深度组件压缩包包含非安全路径或符号链接")
                seen.add(target)
                targets.append((entry, target, mode))
            used = 0
            for entry, target, mode in targets:
                progress("解压深度处理组件", 10)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("xb") as output:
                    while block := source.read(256 * 1024):
                        progress("解压深度处理组件", 10)
                        used += len(block)
                        if used > maximum or used > expanded:
                            raise RuntimeError("深度组件实际解压体积超过限制")
                        output.write(block)
                if mode & 0o111:
                    target.chmod(0o750)
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeError("解压深度处理组件失败") from error


@contextmanager
def installation_lock(root: Path, progress: Progress) -> Iterator[None]:
    """同一持久目录的多个Worker共享文件锁；取消时不继续等待安装。"""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".installation.lock").open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        acquired = False
        try:
            while not acquired:
                progress("等待深度组件安装", 1)
                lock.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    time.sleep(0.1)
            yield
        finally:
            if acquired:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock, fcntl.LOCK_UN)


MAC_FALLBACK = {
    "version": 1,
    "runtime": {
        "urls": [
            "https://github.com/glanderness/BeefTV/releases/download/v1.5.5/"
            "beeftv-depth-runtime-v1-darwin-arm64.zip"
        ],
        "size": 294629974,
        "sha256": "f606dee084ec9d38d76a80e409e4cb9a80ad7db51c18778285a63e8f24376682",
        "files": 23481,
        "expandedSize": 955790953,
    },
    "model": {
        "urls": [
            "https://github.com/glanderness/BeefTV/releases/download/v1.5.5/"
            "video_depth_anything_vits.pth",
            "https://huggingface.co/depth-anything/Video-Depth-Anything-Small/resolve/main/"
            "video_depth_anything_vits.pth",
        ],
        "size": 116440756,
        "sha256": "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609",
    },
}


def publish_runtime(stage: Path, runtime_root: Path, allowed_root: Path) -> None:
    allowed_root = allowed_root.resolve()
    if not stage.resolve().is_relative_to(allowed_root) or not (
        runtime_root.resolve().is_relative_to(allowed_root)
    ):
        raise RuntimeError("深度组件发布目录超出持久目录范围")
    backup = runtime_root.parent / (".depth-runtime-backup-" + uuid4().hex)
    if runtime_root.exists():
        runtime_root.rename(backup)
    try:
        stage.rename(runtime_root)
    except OSError as error:
        if backup.exists():
            try:
                backup.rename(runtime_root)
            except OSError as restore_error:
                raise RuntimeError(
                    f"发布深度组件失败，旧版保留在 {backup}，自动恢复失败"
                ) from restore_error
        raise RuntimeError("发布深度组件失败，旧版已恢复") from error
    if backup.exists():
        if not backup.resolve().is_relative_to(allowed_root):
            raise RuntimeError("旧版深度组件备份目录超出持久目录范围")
        shutil.rmtree(backup)


class CanvasDepthRuntime:
    def __init__(self, settings: Settings, progress: Progress) -> None:
        self.settings, self.progress = settings, progress

    def _manifest(self, platform: str) -> DepthManifest:
        windows = platform == "windows-amd64"
        key = b""
        if windows:
            try:
                key = base64.b64decode(
                    self.settings.beeftv_depth_manifest_public_key.strip(), validate=True
                )
            except ValueError as error:
                raise RuntimeError("Windows 深度处理组件可信发布公钥无效") from error
            if len(key) != 32:
                raise RuntimeError("Windows 深度处理组件尚未配置可信发布公钥，暂不可用")
        url = self.settings.beeftv_depth_manifest_url.strip() or (
            WINDOWS_MANIFEST if windows else MAC_MANIFEST
        )
        last_error: Exception | None = None
        for _ in range(3):
            self.progress("读取深度组件清单", 1)
            try:
                with httpx.stream("GET", url, timeout=15, follow_redirects=True) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"深度组件清单返回 HTTP {response.status_code}")
                    raw = bytearray()
                    for block in response.iter_bytes():
                        self.progress("读取深度组件清单", 1)
                        raw.extend(block)
                        if len(raw) > 1024**2:
                            raise RuntimeError("深度组件清单超过大小限制")
                manifest = (
                    verify_manifest(bytes(raw), key)
                    if windows
                    else (DepthManifest.model_validate_json(raw))
                )
                if manifest.version != (2 if windows else 1):
                    raise RuntimeError("深度组件清单版本不符")
                return manifest
            except (httpx.HTTPError, ValueError, RuntimeError) as error:
                last_error = error
        if not windows:
            return DepthManifest.model_validate(MAC_FALLBACK)
        raise RuntimeError("获取深度组件清单失败，请检查网络及可信发布配置") from last_error

    def _download(self, artifact: DepthArtifact, target: Path, component: str) -> None:
        if verify_artifact(target, artifact, self.progress):
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if artifact.parts:
            if sum(part.size for part in artifact.parts) != artifact.size or any(
                part.size >= 2 * 1024**3 for part in artifact.parts
            ):
                raise RuntimeError("深度组件分片大小与完整包不一致")
            paths = []
            for index, part in enumerate(artifact.parts):
                path = Path(f"{target}.part-{index + 1:03d}")
                self._download(DepthArtifact.model_validate(part.model_dump()), path, component)
                paths.append(path)
            temporary = Path(str(target) + ".assemble")
            try:
                with temporary.open("wb") as output:
                    for path in paths:
                        with path.open("rb") as source:
                            while block := source.read(256 * 1024):
                                self.progress("重组深度处理组件", 10)
                                output.write(block)
                if not verify_artifact(temporary, artifact, self.progress):
                    raise RuntimeError("重组深度组件校验失败")
                temporary.replace(target)
                for path in paths:
                    path.unlink(missing_ok=True)
            finally:
                temporary.unlink(missing_ok=True)
            return
        if not artifact.urls:
            raise RuntimeError("深度组件缺少下载地址")
        temporary = Path(str(target) + ".download")
        if verify_artifact(temporary, artifact, self.progress):
            temporary.replace(target)
            return
        last_error: Exception | None = None
        for url in artifact.urls:
            for _ in range(3):
                self.progress("下载深度处理组件", 2)
                offset = temporary.stat().st_size if temporary.exists() else 0
                if offset > artifact.size:
                    offset = 0
                try:
                    with httpx.stream(
                        "GET",
                        url,
                        headers={"Range": f"bytes={offset}-"} if offset else {},
                        timeout=httpx.Timeout(30, connect=15),
                        follow_redirects=True,
                    ) as response:
                        if response.status_code not in {200, 206}:
                            raise RuntimeError(f"深度组件下载源返回 HTTP {response.status_code}")
                        if response.status_code != 206:
                            offset = 0
                        elif not response.headers.get("content-range", "").startswith(
                            f"bytes {offset}-"
                        ):
                            raise RuntimeError("深度组件下载分片范围不符")
                        with temporary.open("ab" if offset else "wb") as output:
                            # 每个网络块都检查取消；低速下载不能等待攒满256KiB。
                            for block in response.iter_bytes():
                                offset += len(block)
                                if offset > artifact.size:
                                    raise RuntimeError("深度组件下载体积超过发布清单")
                                output.write(block)
                                percent = offset * 100 // artifact.size
                                name = "Small 模型" if component == "model" else "深度处理组件"
                                self.progress(
                                    f"下载{name} {offset / 1024**2:.1f} / "
                                    f"{artifact.size / 1024**2:.1f} MB（{percent}%）",
                                    12 + min(8, percent * 8 // 100)
                                    if component == "model"
                                    else min(12, percent // 10 + 1),
                                )
                    if verify_artifact(temporary, artifact, self.progress):
                        temporary.replace(target)
                        return
                    if temporary.stat().st_size >= artifact.size:
                        temporary.replace(Path(str(target) + ".corrupt"))
                        last_error = RuntimeError("深度组件 SHA-256 校验失败")
                        break
                    last_error = RuntimeError("深度组件下载不完整")
                except (OSError, httpx.HTTPError, RuntimeError) as error:
                    last_error = error
        raise RuntimeError("深度组件下载失败，请检查网络后恢复") from last_error

    def resolve(self, platform: str, variant: str) -> DepthInstallation:
        stopped = Event()
        latest = ("检查深度处理组件", 1)

        def report(stage: str, percent: int) -> None:
            nonlocal latest
            if stopped.is_set():
                raise RenderCancelled()
            latest = stage, percent

        # 网络等待与大包校验期间在调用线程续租；安装线程不持有DB Session。
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="canvas-depth-install") as pool:
            worker = CanvasDepthRuntime(self.settings, report)
            future = pool.submit(worker._resolve, platform, variant)
            started = time.monotonic()
            try:
                while not future.done():
                    self.progress(*latest)
                    if time.monotonic() - started > self.settings.render_timeout_seconds:
                        raise RuntimeError("准备深度组件超时，请检查网络及缓存后恢复")
                    time.sleep(0.1)
                self.progress(*latest)
                return future.result()
            finally:
                stopped.set()

    def _resolve(self, platform: str, variant: str) -> DepthInstallation:
        configured = (
            self.settings.beeftv_depth_python.strip(),
            self.settings.beeftv_depth_tool_dir.strip(),
            self.settings.beeftv_depth_runtime.strip(),
        )
        if all(configured):
            installation = DepthInstallation(
                *(Path(value).expanduser().resolve() for value in configured)
            )
            if (
                not installation.python.is_file()
                or not (installation.tool_dir / "depth_capture").is_dir()
                or not installation.model_runtime.is_dir()
            ):
                raise RuntimeError("独立深度处理组件路径不存在，请检查 BEEFTV_DEPTH 配置")
            return installation
        if any(configured):
            raise RuntimeError("独立深度组件必须同时配置 Python、工具目录及模型目录")
        root = Path(self.settings.canvas_depth_runtime_root).expanduser().resolve()
        runtime_root, archive = runtime_layout(root, platform, variant)
        with installation_lock(root, self.progress):
            manifest = self._manifest(platform)
            artifact = (
                manifest.runtimes.get(f"{platform}/{variant}")
                if platform == "windows-amd64"
                else manifest.runtime
            )
            if artifact is None or (
                platform == "windows-amd64" and not (artifact.files and artifact.expanded_size)
            ):
                raise RuntimeError("深度组件清单缺少对应运行包或解压体积")
            python_relative = (
                ".python/python.exe" if platform == "windows-amd64" else ".venv/bin/python"
            )
            python = runtime_root / python_relative
            tool_dir = runtime_root / "worker"
            archive_ready = verify_artifact(archive, artifact, self.progress)
            if (
                not archive_ready
                or not python.is_file()
                or not (tool_dir / "depth_capture").is_dir()
            ):
                self._download(artifact, archive, "runtime")
                runtime_root.parent.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(
                    prefix=".depth-stage-", dir=runtime_root.parent
                ) as temporary:
                    stage = Path(temporary) / "runtime"
                    stage.mkdir()
                    extract_runtime(
                        archive,
                        stage,
                        artifact,
                        (12 if platform == "windows-amd64" else 3) * 1024**3,
                        self.progress,
                    )
                    if (
                        not (stage / python_relative).is_file()
                        or not (stage / "worker/depth_capture").is_dir()
                    ):
                        raise RuntimeError("深度组件缺少独立 Python 或 worker")
                    if platform != "windows-amd64":
                        (stage / python_relative).chmod(0o750)
                        bundled = stage / ".python/bin/python3.11"
                        if not bundled.is_file() or not os.access(bundled, os.X_OK):
                            raise RuntimeError("深度组件内置 Python 不可执行")
                    self.progress("发布深度处理组件", 12)
                    publish_runtime(stage, runtime_root, root)
            model_runtime = root / "models/video-depth-anything-small/v1"
            self._download(
                manifest.model, model_runtime / "checkpoints/video_depth_anything_vits.pth", "model"
            )
            return DepthInstallation(python, tool_dir, model_runtime)
