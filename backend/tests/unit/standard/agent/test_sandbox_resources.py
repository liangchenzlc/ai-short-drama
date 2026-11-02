"""Resource paths and general files cannot escape the authorized workspace."""

import hashlib
import importlib
import io
from contextlib import contextmanager

import pytest

from short_drama.agent.resources.contracts import ResourceRef
from short_drama.core.exceptions import BusinessError
from short_drama.service.media.files.storage import StorageService


def resources():
    try:
        return importlib.import_module("short_drama.agent.sandbox.resources")
    except ModuleNotFoundError:
        pytest.fail("缺少 scope 沙箱资源交接实现")


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:/secret", "a\\b", "a/../b", ""])
def test_import_export_reject_nonrelative_paths_before_io(path):
    with pytest.raises(BusinessError):
        resources().workspace_path(path)


def test_skill_materialization_paths_separate_scope_activation_and_frozen_version():
    module = resources()
    first = module.skill_path("71", "abc", "scripts/run.py", "a" * 64)
    other = module.skill_path("72", "abc", "scripts/run.py", "a" * 64)
    version = module.skill_path("71", "abc", "scripts/run.py", "b" * 64)
    assert first.startswith(".skills/71/abc/") and first.endswith("/scripts/run.py")
    assert len({first, other, version}) == 3


class Objects:
    def __init__(self):
        self.objects = {}

    def stat(self, bucket, key):
        from short_drama.core.exceptions import NotFound

        if (bucket, key) not in self.objects:
            raise NotFound("missing")
        return self.objects[bucket, key]

    def put(self, bucket, key, data, length, content_type):
        from short_drama.storage.models import StoredObject

        self.data = data.read()
        stored = StoredObject(
            bucket=bucket,
            object_name=key,
            storage_locator=f"minio://{bucket}/{key}",
            size=length,
            content_type=content_type,
        )
        self.objects[bucket, key] = stored
        return stored


def test_freeze_general_file_is_immutable_without_mediafile():
    from types import SimpleNamespace

    objects = Objects()
    storage = StorageService(
        objects,
        SimpleNamespace(
            minio_image_bucket="image", minio_audio_bucket="audio", minio_video_bucket="video"
        ),
    )
    stored = storage.freeze_agent_resource(
        b"a,b\n1,2\n", invocation_id="operation:19", content_type="text/csv"
    )
    again = storage.freeze_agent_resource(
        b"a,b\n1,2\n", invocation_id="operation:19", content_type="text/csv"
    )
    assert stored == again and objects.data == b"a,b\n1,2\n"
    assert "/agent-harness/operation/19/" in stored.storage_locator
    reference = ResourceRef(
        kind="file",
        ref_id="sandbox:19:result.csv",
        uri=stored.storage_locator,
        source="sandbox_artifact",
        checksum=hashlib.sha256(objects.data).hexdigest(),
    )
    assert reference.kind == "file"


def test_unknown_file_is_never_model_hydrated():
    from short_drama.agent.resources.contracts import HydrationIntent
    from short_drama.agent.resources.resolver import ResourceResolver

    class Storage:
        @contextmanager
        def open(self, _locator):
            pytest.fail("未知二进制不能被模型水合")
            yield io.BytesIO(b"binary")

    reference = ResourceRef(
        kind="file", ref_id="f", uri="minio://image/f", source="sandbox_artifact"
    )
    with pytest.raises(BusinessError):
        ResourceResolver(Storage()).freeze(
            HydrationIntent(
                resource=reference, purpose="inspect", tool_call_id="c", invocation_id="turn:1"
            )
        )


def test_export_text_mime_does_not_inherit_windows_registry_file_associations():
    module = resources()
    assert getattr(module, "artifact_mime", lambda _: None)("result.csv") == "text/csv"


def test_streamed_unicode_truncation_keeps_model_and_event_output_within_byte_limit():
    from threading import Lock
    from types import SimpleNamespace

    from short_drama.agent.sandbox.backend import CommandBackend, sandbox_call

    events = []

    def run(_command, *, opts, handlers):
        handlers.on_stdout(SimpleNamespace(text="汉" * 21846))
        return SimpleNamespace(id="execution", exit_code=0)

    backend = CommandBackend.__new__(CommandBackend)
    backend.workspace = SimpleNamespace(
        context=SimpleNamespace(settings=SimpleNamespace(agent_sandbox_output_max_bytes=65536)),
        admitted=lambda *_: None,
    )
    backend.scope_id, backend.fence = 1, (1, "lease")
    backend._sandbox = SimpleNamespace(commands=SimpleNamespace(run=run))
    backend._state_lock = Lock()
    backend._event = lambda name, payload, call: events.append((name, payload))
    backend._archive = lambda *_: None
    token = sandbox_call.set((None, "call", "execute"))
    try:
        result = backend._run("unicode-output", 120)
    finally:
        sandbox_call.reset(token)
    assert result.truncated and len(result.output.encode("utf-8")) <= 65536
    assert (
        sum(
            len(payload["content"].encode("utf-8"))
            for name, payload in events
            if name == "sandbox.log"
        )
        <= 65536
    )


def test_workspace_scope_rejects_missing_execution_fence(monkeypatch):
    from types import SimpleNamespace

    from short_drama.agent.core.access import LeaseLost
    from short_drama.agent.sandbox import workspace

    run = SimpleNamespace(initiated_by=1, lease_token="live", cancel_requested=False, id=1)
    monkeypatch.setattr(workspace, "lock_run", lambda *_: (None, None, run))
    monkeypatch.setattr(workspace, "may_decide", lambda *_: True)
    service = workspace.SandboxWorkspaceService(SimpleNamespace(run_id=1, owner_id=1))
    with pytest.raises(LeaseLost):
        service._scope(SimpleNamespace(scalar=lambda _: object()), 1, None)
