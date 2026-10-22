"""本机真实HTTP上的签名组件下载/断点续传；包内为未执行的占位文件。"""

import base64
import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from short_drama.core.config import Settings
from short_drama.service.canvas_depth_runtime import CanvasDepthRuntime, DepthArtifact
from short_drama.service.video_render import RenderCancelled

pytestmark = pytest.mark.integration


@contextmanager
def runtime_http(*, bad_signature=False, corrupt_archive=False):
    content = io.BytesIO()
    files = {
        ".python/python.exe": b"unexecuted placeholder",
        "worker/depth_capture/__init__.py": b"unexecuted placeholder",
    }
    with zipfile.ZipFile(content, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    archive = content.getvalue()
    private = Ed25519PrivateKey.generate()
    received = []
    base = ""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("Range")))
            if self.path == "/manifest":
                manifest = {
                    "version": 2,
                    "runtimes": {
                        "windows-amd64/cpu": {
                            "urls": [base + "/archive"],
                            "size": len(archive),
                            "sha256": hashlib.sha256(archive).hexdigest(),
                            "files": len(files),
                            "expandedSize": sum(len(data) for data in files.values()),
                        }
                    },
                    "model": {
                        "urls": [base + "/model"],
                        "size": 5,
                        "sha256": hashlib.sha256(b"model").hexdigest(),
                    },
                }
                payload = json.dumps(manifest).encode()
                signature = private.sign(payload)
                if bad_signature:
                    signature = b"x" * 64
                data = json.dumps(
                    {
                        "payload": base64.b64encode(payload).decode(),
                        "signature": base64.b64encode(signature).decode(),
                    }
                ).encode()
            elif self.path == "/archive":
                data = (b"x" * len(archive)) if corrupt_archive else archive
            elif self.path == "/model":
                data = b"model"
            else:
                self.send_error(404)
                return
            offset = int(self.headers["Range"][6:-1]) if self.headers.get("Range") else 0
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header("Content-Range", f"bytes {offset}-{len(data) - 1}/{len(data)}")
            self.send_header("Content-Length", str(len(data) - offset))
            self.end_headers()
            self.wfile.write(data[offset:])

        def log_message(self, *_):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, name="controlled-depth-components", daemon=True)
    thread.start()
    try:
        yield base, private.public_key().public_bytes_raw(), archive, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def runtime(root, base, key):
    settings = Settings(
        _env_file=None,
        canvas_depth_runtime_root=str(root),
        beeftv_depth_manifest_url=base + "/manifest",
        beeftv_depth_manifest_public_key=base64.b64encode(key).decode(),
    )
    return CanvasDepthRuntime(settings, lambda *_: None)


@pytest.mark.parametrize("partial", [False, True])
def test_signed_installation_resumes_bytes_and_reuses_verified_cache(tmp_path, partial):
    with runtime_http() as (base, key, data, received):
        if partial:
            path = tmp_path / "downloads/depth-runtime-v1-windows-amd64-cpu.zip.download"
            path.parent.mkdir()
            path.write_bytes(data[:32])
        install = runtime(tmp_path, base, key).resolve("windows-amd64", "cpu")
        assert install.python.read_bytes() == b"unexecuted placeholder"
        assert (
            install.model_runtime / "checkpoints/video_depth_anything_vits.pth"
        ).read_bytes() == b"model"
        assert ("/archive", "bytes=32-") in received if partial else ("/archive", None) in received
        assert runtime(tmp_path, base, key).resolve("windows-amd64", "cpu") == install
        assert len([row for row in received if row[0] == "/archive"]) == 1
        assert len([row for row in received if row[0] == "/model"]) == 1
        assert len([row for row in received if row[0] == "/manifest"]) == 2


@pytest.mark.parametrize("signature", [True, False])
def test_invalid_release_never_publishes_component(tmp_path, signature):
    with runtime_http(bad_signature=signature, corrupt_archive=not signature) as (
        base,
        key,
        _,
        received,
    ):
        with pytest.raises(RuntimeError):
            runtime(tmp_path, base, key).resolve("windows-amd64", "cpu")
        assert not list(tmp_path.glob("runtimes/**/python.exe"))
        assert not any(row[0] == "/model" for row in received)
        if signature:
            assert all(row[0] == "/manifest" for row in received)
        else:
            assert list(tmp_path.glob("downloads/*.corrupt"))


def test_small_download_chunk_observes_cancellation_before_waiting_for_large_buffer(tmp_path):
    payload = b"x" * (512 * 1024)
    released, observed = Event(), Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[:1024])
            self.wfile.flush()
            released.wait(5)
            try:
                self.wfile.write(payload[1024:])
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def log_message(self, *_):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    artifact = DepthArtifact(
        urls=[f"http://127.0.0.1:{server.server_port}/weights"],
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    def cancel_on_received_chunk(stage, _):
        if stage.startswith("下载深度处理组件 "):
            observed.set()
            raise RenderCancelled()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                CanvasDepthRuntime(Settings(_env_file=None), cancel_on_received_chunk)._download,
                artifact,
                tmp_path / "weights",
                "runtime",
            )
            try:
                assert observed.wait(3), "下载必须在收到小块字节后检查取消，不能等待256KiB"
                with pytest.raises(RenderCancelled):
                    future.result(timeout=3)
                assert not (tmp_path / "weights").exists()
                assert (tmp_path / "weights.download").read_bytes() == payload[:1024]
            finally:
                released.set()
    finally:
        released.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
