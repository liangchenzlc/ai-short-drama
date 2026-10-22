"""固定深度组件的签名、归档边界及缓存验证；不运行深度模型。"""

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from short_drama.service.canvas_depth_runtime import (
    DepthArtifact,
    extract_runtime,
    publish_runtime,
    runtime_layout,
    verify_artifact,
    verify_manifest,
)


def signed_manifest(private, **patch):
    manifest = {
        "version": 2,
        "runtimes": {
            "windows-amd64/cpu": {
                "urls": ["https://example.invalid/runtime"],
                "size": 3,
                "sha256": hashlib.sha256(b"zip").hexdigest(),
                "files": 2,
                "expandedSize": 7,
            }
        },
        "model": {
            "urls": ["https://example.invalid/model"],
            "size": 5,
            "sha256": hashlib.sha256(b"model").hexdigest(),
        },
        **patch,
    }
    payload = json.dumps(manifest).encode()
    return json.dumps(
        {
            "payload": base64.b64encode(payload).decode(),
            "signature": base64.b64encode(private.sign(payload)).decode(),
        }
    ).encode()


def test_manifest_accepts_only_exact_signed_payload():
    private = Ed25519PrivateKey.generate()
    raw = signed_manifest(private)
    public = private.public_key().public_bytes_raw()
    assert verify_manifest(raw, public).version == 2
    envelope = json.loads(raw)
    envelope["payload"] = base64.b64encode(b"{}").decode()
    with pytest.raises(RuntimeError, match="签名"):
        verify_manifest(json.dumps(envelope).encode(), public)
    for data, key in (
        (raw, Ed25519PrivateKey.generate().public_key().public_bytes_raw()),
        (raw, b""),
        (signed_manifest(private, unexpected=True), public),
        (signed_manifest(private, version=1), public),
        (raw + b"{}", public),
        (b"x" * (1024**2 + 1), public),
    ):
        with pytest.raises(RuntimeError):
            verify_manifest(data, key)


@pytest.mark.parametrize(
    "platform,variant",
    [
        ("linux-amd64", "cpu"),
        ("windows-amd64", "mps"),
        ("darwin-arm64", "cuda"),
        ("windows-arm64", "cpu"),
    ],
)
def test_unsupported_runtime_layout(tmp_path, platform, variant):
    with pytest.raises(RuntimeError, match="不支持"):
        runtime_layout(tmp_path, platform, variant)


def test_cached_artifact_rejects_changed_length_and_digest(tmp_path):
    path = tmp_path / "model"
    artifact = DepthArtifact(
        urls=["https://example.invalid/model"], size=5, sha256=hashlib.sha256(b"model").hexdigest()
    )
    path.write_bytes(b"model")
    assert verify_artifact(path, artifact, lambda *_: None)
    for data in (b"bad", b"other"):
        path.write_bytes(data)
        assert not verify_artifact(path, artifact, lambda *_: None)


@pytest.mark.parametrize(
    "name", ["../escape", r"..\escape", "/absolute", "C:/absolute", "a/../../escape"]
)
def test_archive_rejects_paths_outside_stage(tmp_path, name):
    path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"data")
    artifact = DepthArtifact(
        urls=["https://example.invalid/zip"],
        size=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        files=1,
        expanded_size=4,
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(RuntimeError, match="路径"):
        extract_runtime(path, stage, artifact, 1024, lambda *_: None)
    assert list(stage.iterdir()) == []


def test_archive_preserves_files_and_checks_release_shape(tmp_path):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(r"worker\depth_capture\__init__.py", b"source")
    path = tmp_path / "runtime.zip"
    path.write_bytes(content.getvalue())
    artifact = DepthArtifact(
        urls=["https://example.invalid/zip"],
        size=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        files=1,
        expanded_size=6,
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    extract_runtime(path, stage, artifact, 1024, lambda *_: None)
    assert (stage / "worker/depth_capture/__init__.py").read_bytes() == b"source"
    for patch in ({"files": 2}, {"expanded_size": 7}):
        with pytest.raises(RuntimeError):
            extract_runtime(
                path, tmp_path / "other", artifact.model_copy(update=patch), 1024, lambda *_: None
            )


@pytest.mark.parametrize("restore_fails", [False, True])
def test_publish_failure_preserves_previous_installation(tmp_path, monkeypatch, restore_fails):
    root, stage = tmp_path / "runtime", tmp_path / "stage"
    root.mkdir()
    stage.mkdir()
    (root / "previous").write_bytes(b"old")
    (stage / "new").write_bytes(b"new")
    original = Path.rename

    def fail(path, target):
        if path == stage or (restore_fails and path.name.startswith(".depth-runtime-backup-")):
            raise OSError("controlled publication failure")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(RuntimeError, match="保留" if restore_fails else "已恢复"):
        publish_runtime(stage, root, tmp_path)
    if restore_fails:
        backups = list(tmp_path.glob(".depth-runtime-backup-*"))
        assert len(backups) == 1 and (backups[0] / "previous").read_bytes() == b"old"
    else:
        assert (root / "previous").read_bytes() == b"old"
    assert (stage / "new").read_bytes() == b"new"
