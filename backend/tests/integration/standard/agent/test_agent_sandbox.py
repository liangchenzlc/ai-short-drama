"""Opt-in production scope/backend with real Docker, MySQL and MinIO."""

import importlib
import json
import os
import shlex
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.domain.assistant.conversations.models import AgentConversation, AgentEvent
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.tasks.task import AgentOperation
from tests.integration.standard.assistant.test_agent_context_compaction import runtime
from tests.integration.standard.assistant.test_execution_scopes import calls_for
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import new_task, task_service
from tests.support.assistant_runtime import RuntimeTransport, calls, tool_result

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_AGENT_SANDBOX_INTEGRATION") != "1",
        reason="需要显式启用真实沙箱基础设施",
    ),
]


@pytest.fixture(autouse=True)
def cleanup_test_runs(harness_workspace):
    yield
    from short_drama.agent.sandbox.workspace import cleanup_terminal_runs
    from short_drama.agent.session.purge import purge_one
    from short_drama.service.media.files.storage import StorageService
    from short_drama.storage.minio import MinioStorage

    factory, _, _ = harness_workspace
    settings = Settings()
    with factory() as session:
        runs = session.scalars(select(AgentRun)).all()
    for run in runs:
        if run.status not in {"succeeded", "failed", "cancelled"}:
            with factory() as session:
                task_service(session, settings).control_task(
                    run.task_id, {"kind": "stop"}, uuid4().hex
                )
    cleanup_terminal_runs(factory, settings)
    storage = MinioStorage(settings)
    try:
        managed = StorageService(storage, settings)
        with factory() as session:
            conversation_ids = {run.conversation_id for run in runs}
            conversations = [
                (row.id, row.status, row.row_version)
                for row in session.scalars(
                    select(AgentConversation).where(AgentConversation.id.in_(conversation_ids))
                )
            ]
        for identifier, status, version in conversations:
            if status == "deleted":
                continue
            with factory() as session:
                service = task_service(session, settings)
                service.delete_global_conversation(
                    identifier, {"row_version": version}, uuid4().hex
                )
        while purge_one(factory, managed):
            pass
    finally:
        storage.close()


def test_trusted_recovery_reclaims_deleted_run_and_rejects_wrong_scope_directory(
    harness_workspace, tmp_path
):
    from types import SimpleNamespace

    from opensandbox import SandboxSync
    from opensandbox.models.sandboxes import Host, Volume
    from pydantic import SecretStr

    from short_drama.agent.coordinator.recovery import recover
    from short_drama.agent.sandbox.workspace import cleanup_run
    from short_drama.service.assistant.execution.run import AgentRunService

    factory, _, model = harness_workspace
    settings = Settings()
    configured = (factory, settings, model)
    conversation, accepted = new_task(configured)
    _, context = runtime(configured, accepted)
    service = workspace(context)
    root = service.backend(context.scope_id, fence=context.fence)
    assert root.execute("echo root > marker.txt").exit_code == 0
    parents = calls_for(factory, accepted, context.scope_id)
    child = context.scopes.child(context.scope_id, parents[0])
    sibling = service.backend(child.id, fence=context.fence)
    assert sibling.execute("echo sibling > marker.txt").exit_code == 0
    with factory.begin() as session:
        root_scope = session.get(AgentExecutionScope, context.scope_id)
        root_path = root_scope.workspace_volume
        sibling_path = session.get(AgentExecutionScope, child.id).workspace_volume
        root_scope.workspace_volume = sibling_path
    with factory() as session:
        AgentRunService(session, settings).finish(
            SimpleNamespace(run_id=context.run_id, fence=context.fence),
            status="failed",
            error={"code": "test_terminal"},
        )
    assert not cleanup_run(factory, settings, context.run_id, workspace=service)
    assert (
        sibling._sandbox.commands.run("cat /workspace/marker.txt").logs.stdout[0].text == "sibling"
    )
    with factory.begin() as session:
        scope = session.get(AgentExecutionScope, context.scope_id)
        assert scope.sandbox_status == "cleanup_pending"
        scope.workspace_volume = root_path
    wrong = settings.model_copy(update={"agent_sandbox_api_key": SecretStr("invalid-test-key")})
    assert not cleanup_run(factory, wrong, context.run_id)
    with factory() as session:
        manager = task_service(session, settings)
        current = manager.get_conversation(conversation.id)
        assert (
            manager.delete_global_conversation(
                conversation.id, {"row_version": current.row_version}, uuid4().hex
            ).status
            == "deleted"
        )
    # deleted + 无租约 + Agent关闭仍可通过可信维护查询回收，无需放松普通图授权。
    recover(factory, settings.model_copy(update={"agent_enabled": False}))
    with factory() as session:
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == context.run_id)
        ).all()
        assert all(
            scope.sandbox_status == "closed" and scope.sandbox_id is None for scope in scopes
        )
    verifier = SandboxSync.create(
        settings.agent_sandbox_image,
        connection_config=service.connection(),
        metadata={"app": "short-drama-assistant-cleanup-test", "run": str(context.run_id)},
        volumes=[
            Volume(
                name="verify",
                host=Host(path=settings.agent_sandbox_workspace_root),
                mount_path="/verify",
                read_only=True,
            )
        ],
    )
    try:
        result = verifier.commands.run(
            f"test ! -e /verify/run-{context.run_id} && echo RUN_DIRECTORY_REMOVED"
        )
        assert result.exit_code == 0
        tmp_path.joinpath("cleanup-evidence.json").write_text(
            json.dumps(
                {
                    "run": str(context.run_id),
                    "paths": [root_path, sibling_path],
                    "wrong_binding_rejected": True,
                    "auth_failure_pending": True,
                    "deleted_recovery": True,
                    "directory_absent": True,
                }
            ),
            encoding="utf-8",
        )
    finally:
        verifier.destroy()
        service.close()


def workspace(context):
    try:
        implementation = importlib.import_module("short_drama.agent.sandbox.workspace")
    except ModuleNotFoundError:
        pytest.fail("缺少真实 OpenSandbox scope workspace 生命周期")
    return implementation.SandboxWorkspaceService(context)


def test_real_workspace_media_export_has_media_identity_and_private_purge(
    harness_workspace, tmp_path
):
    import base64
    import hashlib
    import io
    import subprocess
    import wave

    from PIL import Image

    from short_drama.agent.runtime.executor import AgentRuntime
    from short_drama.agent.session.purge import purge_one
    from short_drama.core.exceptions import NotFound
    from short_drama.domain import AgentAttachment, MediaFile
    from short_drama.service.media.files.storage import StorageService
    from short_drama.service.media.processing.render_tools import executable
    from short_drama.storage.minio import MinioStorage

    factory, _, model = harness_workspace
    settings = Settings()
    image = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(image, "PNG")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(b"\x00\x00" * 800)
    video = tmp_path / "result.mp4"
    subprocess.run(
        [
            executable(settings.render_ffmpeg_path),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=16x16:r=5:d=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    samples = {
        "result.png": image.getvalue(),
        "result.wav": audio.getvalue(),
        "result.mp4": video.read_bytes(),
    }
    script = "import base64,pathlib; " + "; ".join(
        f"pathlib.Path({name!r}).write_bytes(base64.b64decode({base64.b64encode(data).decode()!r}))"
        for name, data in samples.items()
    )
    conversation, accepted = new_task((factory, settings, model))
    observed = []

    def complete(body):
        observed.append(tool_result(body, "media-export"))
        return {"text": "已归档本地图片、音频和视频"}

    transport = RuntimeTransport(
        [
            calls(("write-media", "execute", {"command": "python -c " + shlex.quote(script)})),
            calls(
                (
                    "media-export",
                    "runtime__export_artifacts",
                    {
                        "paths": [*samples, "result.png"],
                    },
                )
            ),
            complete,
        ]
    )
    AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, 1)
    assert observed and "resources" in observed[0], observed
    references = observed[0]["resources"]
    assert [item["kind"] for item in references] == ["image", "audio", "video", "image"]
    assert references[0] == references[-1]
    client = MinioStorage(settings)
    managed = StorageService(client, settings)
    try:
        with factory() as session:
            operation = session.scalar(
                select(AgentOperation).where(
                    AgentOperation.run_id == accepted.run.id,
                    AgentOperation.tool_name == "runtime__export_artifacts",
                )
            )
            assert session.get(AgentRun, accepted.run.id).status == "succeeded"
            assert len({item["ref_id"] for item in operation.result_refs}) == 3
            assert not session.scalars(select(AgentAttachment)).all()
            rows = session.scalars(select(MediaFile)).all()
            assert len(rows) == 3
            for reference, (name, data) in zip(references[:3], samples.items(), strict=True):
                media = session.get(MediaFile, int(reference["metadata"]["media_id"]))
                assert media.scope_user_id == 1 and media.project_id is None
                assert media.storage_locator == reference["uri"]
                assert media.original_name == name and media.byte_size == len(data)
                assert media.checksum_sha256 == hashlib.sha256(data).hexdigest()
                assert reference["metadata"]["operation_id"] == str(operation.id)
            assert (rows[0].width, rows[0].height) == (2, 3)
            assert rows[1].duration_ms == 100
            assert rows[2].width == 16 and rows[2].height == 16
            assert rows[2].duration_ms == 200 and rows[2].video_metadata
        for reference, data in zip(references[:3], samples.values(), strict=True):
            with managed.open(reference["uri"]) as stream:
                assert stream.read() == data
        with factory() as session:
            service = task_service(session, settings)
            current = service.get_conversation(conversation.id)
            service.delete_global_conversation(
                conversation.id, {"row_version": current.row_version}, uuid4().hex
            )
        assert purge_one(factory, managed)
        with factory() as session:
            assert not session.scalars(select(MediaFile)).all()
            assert session.get(AgentOperation, operation.id).result_refs == []
        for reference in references[:3]:
            with pytest.raises(NotFound):
                managed.stat(reference["uri"])
    finally:
        for reference in references[:3]:
            managed.delete(reference["uri"])
        client.close()


def media_export_context(harness_workspace):
    from short_drama.agent.task.state import apply_inputs_locked
    from short_drama.domain.assistant.execution.models import AgentToolCall
    from short_drama.domain.assistant.tasks.task import AgentTask
    from tests.integration.standard.assistant.test_business_invocations import scope_operation

    factory, _, model = harness_workspace
    configured = factory, Settings(), model
    conversation, accepted = new_task(configured)
    _, context = runtime(configured, accepted)
    with factory.begin() as session:
        task = session.get(AgentTask, accepted.task.id)
        run = session.get(AgentRun, accepted.run.id)
        assert apply_inputs_locked(
            session, session.get(AgentConversation, conversation.id), task, run
        )
    operation_id = scope_operation(factory, accepted, context.scope_id, "runtime__export_artifacts")
    with factory() as session:
        call_id = session.get(
            AgentToolCall, session.get(AgentOperation, operation_id).tool_call_id
        ).provider_call_id
    return conversation, accepted, context, call_id, operation_id


def png_sample():
    import io

    from PIL import Image

    data = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(data, "PNG")
    return data.getvalue()


def test_invalid_media_exports_reject_content_without_database_or_storage_residue(
    harness_workspace,
):
    from short_drama.agent.sandbox.resources import publish_media
    from short_drama.core.exceptions import BusinessError
    from short_drama.domain import MediaFile
    from short_drama.storage.minio import MinioStorage

    _, _, context, call_id, operation_id = media_export_context(harness_workspace)
    client = MinioStorage(context.settings)
    buckets = [
        context.settings.minio_image_bucket,
        context.settings.minio_audio_bucket,
        context.settings.minio_video_bucket,
    ]
    try:
        before = {
            item.storage_locator for bucket in buckets for item in client.iter_objects(bucket, "")
        }
        for name, data in [
            ("invalid.png", b"not an image"),
            ("invalid.wav", b"RIFF\x00\x00\x00\x00WAVE" + bytes(64)),
            ("invalid.mp4", b"\x00\x00\x00\x18ftyp" + bytes(64)),
            ("unsupported.gif", png_sample()),
        ]:
            with pytest.raises(BusinessError) as error:
                publish_media(context, call_id, name, data)
            assert error.value.status_code == 422
        with context.factory() as session:
            assert not session.scalars(select(MediaFile)).all()
            assert session.get(AgentOperation, operation_id).result_refs == []
        after = {
            item.storage_locator for bucket in buckets for item in client.iter_objects(bucket, "")
        }
        assert after == before
    finally:
        client.close()


def test_media_export_lost_fence_compensates_real_upload(harness_workspace, monkeypatch):
    from short_drama.agent.core.access import LeaseLost
    from short_drama.agent.sandbox.resources import publish_media
    from short_drama.core.exceptions import NotFound
    from short_drama.domain import MediaFile
    from short_drama.service.media.files.storage import StorageService
    from short_drama.storage.minio import MinioStorage

    _, _, context, call_id, operation_id = media_export_context(harness_workspace)
    uploaded = []
    upload = StorageService.upload

    def revoke_after_upload(storage, *args, **kwargs):
        stored = upload(storage, *args, **kwargs)
        uploaded.append(stored)
        with context.factory.begin() as session:
            session.get(AgentRun, context.run_id).lease_token = "replacement-worker"
        return stored

    monkeypatch.setattr(StorageService, "upload", revoke_after_upload)
    with pytest.raises(LeaseLost):
        publish_media(context, call_id, "result.png", png_sample())
    assert len(uploaded) == 1
    with context.factory() as session:
        assert not session.scalars(select(MediaFile)).all()
        assert session.get(AgentOperation, operation_id).result_refs == []
    client = MinioStorage(context.settings)
    try:
        with pytest.raises(NotFound):
            StorageService(client, context.settings).stat(uploaded[0].storage_locator)
    finally:
        client.close()


def test_exported_media_authorization_and_purge_keep_independent_business_references(
    harness_workspace,
):
    from short_drama.agent.core.access import LeaseLost
    from short_drama.agent.resources.catalog import authorized_reference_locked
    from short_drama.agent.resources.contracts import ResourceRef
    from short_drama.agent.sandbox.resources import publish_media
    from short_drama.agent.session.purge import purge_one
    from short_drama.agent.tools.runtime import execution_locked
    from short_drama.core.exceptions import NotFound
    from short_drama.domain import AgentAttachment, MediaFile
    from short_drama.domain.assistant.tasks.task import AgentTask
    from short_drama.domain.canvas.styles.style import CanvasStyleMediaReference
    from short_drama.schemas.canvas.styles.style import CanvasStyleCreate
    from short_drama.service.assistant.execution.run import AgentRunService
    from short_drama.service.base import utcnow
    from short_drama.service.canvas.styles.style import CanvasStyleService
    from short_drama.service.creation.writing.script.publication import publish
    from short_drama.service.media.files.files import MediaFileService
    from short_drama.service.media.files.storage import StorageService
    from short_drama.storage.minio import MinioStorage
    from short_drama.utils.snowflake import next_id
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    conversation, accepted, context, call_id, _ = media_export_context(harness_workspace)
    data = png_sample()
    with pytest.raises(LeaseLost):
        publish_media(replace(context, owner_id=2), call_id, "foreign.png", data)
    references = [
        publish_media(context, call_id, f"{name}.png", data)
        for name in ("orphan", "attachment", "business", "adopted")
    ]
    ids = [int(item["metadata"]["media_id"]) for item in references]
    assert publish_media(context, call_id, "orphan.png", data) == references[0]
    client = MinioStorage(context.settings)
    managed = StorageService(client, context.settings)
    try:
        with context.factory() as session:
            service = task_service(session, context.settings)
            other = service.create_global_conversation({}, uuid4().hex)
        with context.factory.begin() as session:
            current, task, _, _ = execution_locked(session, context, call_id)
            reference = ResourceRef.model_validate(references[0])
            assert authorized_reference_locked(session, current, task, reference) == reference
            with pytest.raises(NotFound):
                authorized_reference_locked(
                    session,
                    current,
                    task,
                    reference.model_copy(update={"uri": "minio://image/forged.png"}),
                )
            with pytest.raises(NotFound):
                authorized_reference_locked(
                    session, session.get(AgentConversation, other.id), task, reference
                )
            session.add(
                AgentAttachment(
                    id=next_id(),
                    owner_user_id=1,
                    conversation_id=other.id,
                    kind="image",
                    name="independent.png",
                    mime_type="image/png",
                    media_id=ids[1],
                    checksum_sha256=references[1]["checksum"],
                    byte_size=len(data),
                    input_metadata={},
                    created_at=utcnow(),
                )
            )
            publish(session.get(MediaFile, ids[3]))
        with context.factory() as session:
            session.info["actor"] = task_actor()
            CanvasStyleService(session).create_personal(
                CanvasStyleCreate(
                    profileJson=json.dumps(
                        {
                            "schemaVersion": 1,
                            "presetId": "sandbox-media",
                            "title": "沙箱参考画风",
                            "description": "真实媒体引用保留验证",
                            "tags": [],
                            "prompt": "红色画面",
                            "source": "user",
                            "revision": 1,
                            "assets": [
                                {
                                    "id": "reference",
                                    "kind": "reference",
                                    "provider": "local",
                                    "title": "工作区图片",
                                    "status": "validated",
                                    "referenceResourceIds": [str(ids[2])],
                                }
                            ],
                        }
                    )
                )
            )
        with context.factory() as session:
            assert (
                session.scalar(
                    select(CanvasStyleMediaReference.id).where(
                        CanvasStyleMediaReference.media_id == ids[2]
                    )
                )
                is not None
            )
            assert session.get(MediaFile, ids[2]).published_at is None
        with context.factory() as session:
            session.info["actor"] = task_actor(2)
            with pytest.raises(NotFound):
                MediaFileService(session).get(ids[0])
        with context.factory() as session:
            assert AgentRunService(session, context.settings).finish(context, status="cancelled")
        with context.factory() as session:
            service = task_service(session, context.settings)
            current = service.get_conversation(conversation.id)
            service.delete_global_conversation(
                conversation.id, {"row_version": current.row_version}, uuid4().hex
            )
        assert purge_one(context.factory, managed)
        with context.factory() as session:
            assert session.get(MediaFile, ids[0]) is None
            assert all(session.get(MediaFile, identifier) is not None for identifier in ids[1:])
            assert session.get(AgentTask, accepted.task.id).status == "stopped"
        with pytest.raises(NotFound):
            managed.stat(references[0]["uri"])
        for reference in references[1:]:
            with managed.open(reference["uri"]) as stream:
                assert stream.read() == data
    finally:
        for reference in references:
            managed.delete(reference["uri"])
        client.close()


def test_deleted_media_identity_is_no_longer_an_authorized_archive(harness_workspace):
    from short_drama.agent.resources.catalog import authorized_reference_locked
    from short_drama.agent.resources.contracts import ResourceRef
    from short_drama.agent.sandbox.resources import publish_media
    from short_drama.agent.tools.runtime import execution_locked
    from short_drama.core.exceptions import NotFound
    from short_drama.service.media.files.files import MediaFileService
    from short_drama.service.media.files.storage import StorageService
    from short_drama.storage.minio import MinioStorage
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    _, _, context, call_id, _ = media_export_context(harness_workspace)
    reference = ResourceRef.model_validate(
        publish_media(context, call_id, "deleted.png", png_sample())
    )
    client = MinioStorage(context.settings)
    try:
        with context.factory() as session:
            session.info["actor"] = task_actor()
            MediaFileService(session).delete(reference.metadata["media_id"])
        with context.factory.begin() as session:
            conversation, task, _, _ = execution_locked(session, context, call_id)
            with pytest.raises(NotFound):
                authorized_reference_locked(session, conversation, task, reference)
        with pytest.raises(NotFound):
            publish_media(context, call_id, "deleted.png", png_sample())
    finally:
        StorageService(client, context.settings).delete(reference.uri)
        client.close()


def test_create_response_loss_recovers_same_sandbox_and_reconnect_keeps_fence(
    harness_workspace, monkeypatch, tmp_path
):
    import subprocess

    from opensandbox import SandboxSync

    from short_drama.agent.core.access import LeaseLost

    factory, _, model = harness_workspace
    settings = Settings()
    configured = (factory, settings, model)
    _, accepted = new_task(configured)
    _, context = runtime(configured, accepted)
    service = workspace(context)
    create = SandboxSync.create
    created = []

    def lost_response(*args, **kwargs):
        client = create(*args, **kwargs)
        created.append(client.id)
        client.close()
        raise TimeoutError("模拟服务已创建但响应丢失")

    with monkeypatch.context() as patch:
        patch.setattr(SandboxSync, "create", lost_response)
        with pytest.raises(TimeoutError):
            service.ensure(context.scope_id, fence=context.fence)
    with factory() as session:
        scope = session.get(AgentExecutionScope, context.scope_id)
        assert scope.sandbox_status == "creating" and scope.sandbox_id is None
    binding = service.ensure(context.scope_id, fence=context.fence)
    assert created == [binding.id]
    backend = service.backend(context.scope_id, fence=context.fence)
    assert backend.execute("printf reconnected > marker.txt").exit_code == 0
    service.close()
    rebuilt = workspace(context)
    try:
        assert rebuilt.ensure(context.scope_id, fence=context.fence).id == binding.id
        assert (
            rebuilt.backend(context.scope_id, fence=context.fence).execute("cat marker.txt").output
            == "reconnected\n"
        )
        with pytest.raises(LeaseLost):
            rebuilt.ensure(context.scope_id, fence=(context.fence[0], "old-worker"))
        inspected = json.loads(
            subprocess.check_output(["docker", "inspect", f"sandbox-{binding.id}"], text=True)
        )[0]
        host = inspected["HostConfig"]
        mounts = [
            {key: mount[key] for key in ("Source", "Destination", "RW")}
            for mount in inspected["Mounts"]
        ]
        assert host["Memory"] == 2 * 1024**3 and host["NanoCpus"] == 2 * 10**9
        assert host["NetworkMode"] not in {"host", "default", "bridge"}
        assert all(mount["Destination"] != "/var/run/docker.sock" for mount in mounts)
        assert any(mount["Destination"] == "/workspace" and mount["RW"] for mount in mounts)
        assert any(option.startswith("no-new-privileges") for option in host["SecurityOpt"])
        tmp_path.joinpath("binding-evidence.json").write_text(
            json.dumps(
                {
                    "id": binding.id,
                    "create_response_lost": True,
                    "metadata_recovered": True,
                    "reconnected": True,
                    "stale_fence_rejected": True,
                    "network": host["NetworkMode"],
                    "cpu": host["NanoCpus"],
                    "memory": host["Memory"],
                    "user": inspected["Config"]["User"],
                    "mounts": mounts,
                }
            ),
            encoding="utf-8",
        )
    finally:
        rebuilt.cleanup_run(context.run_id, fence=context.fence)
        rebuilt.close()


def test_child_scope_cannot_materialize_parent_skill_activation(harness_workspace):
    from short_drama.agent.sandbox.resources import MaterializeSkillResource, SandboxResourceService
    from short_drama.agent.task.state import apply_inputs_locked
    from short_drama.agent.tools.registry import get_spec
    from short_drama.agent.tools.runtime import invoke_runtime_tool
    from short_drama.core.exceptions import NotFound
    from short_drama.domain.assistant.execution.models import AgentToolCall
    from short_drama.domain.assistant.tasks.task import AgentTask
    from short_drama.domain.skills.catalog.skill import AgentSkillVersion
    from short_drama.service.media.files.storage import StorageService
    from short_drama.service.skills.catalog.skill import AgentSkillService
    from short_drama.storage.minio import MinioStorage
    from tests.integration.standard.assistant.test_business_invocations import scope_operation
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    factory, _, model = harness_workspace
    settings = Settings()
    storage = MinioStorage(settings)
    locators = []
    try:
        with factory() as session:
            session.info["actor"] = task_actor()
            skill = AgentSkillService(session, settings, storage, tool_lookup=get_spec).create(
                {
                    "name": "父范围脚本",
                    "slug": "parent-scope-script",
                    "description": "验证作用域资格",
                    "instructions": "显式物化脚本。",
                    "resources": [
                        {"path": "scripts/check.py", "kind": "script", "content": "print('ok')"}
                    ],
                },
                uuid4().hex,
            )
        with factory() as session:
            versions = session.scalars(select(AgentSkillVersion)).all()
            locators = [
                f"minio://{item['bucket']}/{item['object_name']}"
                for version in versions
                for item in version.resources
            ]
        _, accepted = new_task((factory, settings, model))
        _, context = runtime((factory, settings, model), accepted)
        with factory.begin() as session:
            task = session.get(AgentTask, accepted.task.id)
            run = session.get(AgentRun, context.run_id)
            conversation = session.get(AgentConversation, run.conversation_id)
            assert apply_inputs_locked(session, conversation, task, run)
        parent_calls = calls_for(factory, accepted, context.scope_id)
        child = context.scopes.child(context.scope_id, parent_calls[0])
        arguments = {"name": skill.name, "content_version": "1", "purpose": "检验资格"}
        operation_id = scope_operation(
            factory, accepted, context.scope_id, "runtime__load_skill", arguments
        )
        with factory.begin() as session:
            call_id = session.get(
                AgentToolCall, session.get(AgentOperation, operation_id).tool_call_id
            ).provider_call_id
        state = {"scope_id": str(context.scope_id), "permitted_tool_names": []}
        spec = get_spec("runtime__load_skill")
        loaded = invoke_runtime_tool(
            spec.handler.locked_handler,
            spec.args_schema.model_validate(arguments),
            context,
            call_id,
            state=state,
        )
        operation_id = scope_operation(
            factory, accepted, child.id, "runtime__materialize_skill_resource"
        )
        with factory() as session:
            call_id = session.get(
                AgentToolCall, session.get(AgentOperation, operation_id).tool_call_id
            ).provider_call_id
        from short_drama.agent.runtime.context import execution_scope

        child_context = replace(context, sandbox=workspace(context))
        scope_token = execution_scope.set(child.id)
        try:
            consumer = SandboxResourceService(
                child_context, call_id, state={**state, "scope_id": str(child.id)}
            )
            with pytest.raises(NotFound):
                consumer.materialize_skill_resource(
                    MaterializeSkillResource(
                        activation_id=loaded["activation_id"], path="scripts/check.py"
                    )
                )
        finally:
            execution_scope.reset(scope_token)
        with factory() as session:
            assert session.get(AgentExecutionScope, child.id).sandbox_id is None
    finally:
        for locator in locators:
            StorageService(storage, settings).delete(locator)
        storage.close()


def test_lazy_scope_reuse_isolation_wait_restore_and_terminal_cleanup(harness_workspace):
    factory, _, model = harness_workspace
    settings = Settings()
    configured = (factory, settings, model)
    _, accepted = new_task(configured)
    _, context = runtime(configured, accepted)
    service = workspace(context)
    with factory() as session:
        assert session.get(AgentExecutionScope, context.scope_id).sandbox_id is None
    binding = service.ensure(context.scope_id, fence=context.fence)
    assert service.ensure(context.scope_id, fence=context.fence).id == binding.id
    backend = service.backend(context.scope_id, fence=context.fence)
    try:
        response = backend.execute("python --version && node --version && npm --version")
        assert response.exit_code == 0
        assert "Python 3.12.16" in response.output and "v24.22.2" in response.output
        assert backend.execute("printf persisted > marker.txt").exit_code == 0
        parents = calls_for(factory, accepted, context.scope_id)
        child = context.scopes.child(context.scope_id, parents[0])
        child_backend = service.backend(child.id, fence=context.fence)
        assert (
            child_backend.execute("test ! -e marker.txt && printf child > marker.txt").exit_code
            == 0
        )
        assert backend.execute("cat marker.txt").output.strip() == "persisted"
        service.release_for_wait(context.run_id, fence=context.fence)
        with factory() as session:
            scopes = session.scalars(
                select(AgentExecutionScope).where(AgentExecutionScope.run_id == context.run_id)
            ).all()
            assert all(
                scope.sandbox_status == "released" and scope.sandbox_id is None for scope in scopes
            )
        rebuilt = workspace(replace(context))
        restored = rebuilt.backend(context.scope_id, fence=context.fence)
        assert restored.id != binding.id
        assert restored.execute("cat marker.txt").output.strip() == "persisted"
        rebuilt.cleanup_run(context.run_id, fence=context.fence)
        with factory() as session:
            scopes = session.scalars(
                select(AgentExecutionScope).where(AgentExecutionScope.run_id == context.run_id)
            ).all()
            assert all(scope.sandbox_status == "closed" for scope in scopes)
    finally:
        service.cleanup_run(context.run_id, fence=context.fence)


def test_real_tool_node_online_packages_wait_resume_export_and_deleted_purge(
    harness_workspace, tmp_path
):
    from short_drama.agent.coordinator.recovery import recover
    from short_drama.agent.runtime.executor import AgentRuntime
    from short_drama.agent.session.purge import purge_one
    from short_drama.service.media.files.storage import StorageService
    from short_drama.storage.minio import MinioStorage
    from tests.integration.standard.assistant.test_agent_questions import (
        QUESTION,
        answer,
        execute_next,
    )

    factory, _, model = harness_workspace
    settings = Settings()
    configured = (factory, settings, model)
    conversation, accepted = new_task(configured)
    probe = """import json,os,pathlib,sys,urllib.request
assert not pathlib.Path('/var/run/docker.sock').exists()
keys=['AGENT_SANDBOX_API_KEY','DB_PASSWORD','MINIO_SECRET_KEY','OPENSANDBOX_SERVER_API_KEY']
assert not any(k in os.environ for k in keys)
with urllib.request.urlopen('https://example.com/',timeout=15) as r:
 pathlib.Path('public.html').write_bytes(r.read()); download=r.status
with urllib.request.urlopen('https://httpbin.org/anything/assistant-runtime-task7',timeout=15) as r:
 api=json.load(r); status=r.status
pathlib.Path('result.csv').write_text('name,value\\npython,3.12\\nnode,24\\n')
print(json.dumps({'uid':os.getuid(),'cwd':os.getcwd(),'venv':sys.prefix,'download':download,'http_api':status,'api_url':api['url']}))
"""
    command = (
        "python --version && node --version && "
        "pip install --disable-pip-version-check six==1.17.0 && "
        "npm install --no-audit --no-fund --save-exact is-number@7.0.0 && "
        "python -u -c " + shlex.quote(probe)
    )
    first = RuntimeTransport(
        [
            calls(("online", "execute", {"command": command})),
            calls(("question", "runtime__ask_user", QUESTION)),
        ]
    )
    AgentRuntime(factory, settings, transport=first).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input"
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == run.id)
        ).all()
        assert scopes and all(
            scope.sandbox_id is None and scope.sandbox_status == "released" for scope in scopes
        )
        question = session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id, AgentOperation.tool_name == "runtime__ask_user"
            )
        )
        events = session.scalars(
            select(AgentEvent).where(
                AgentEvent.run_id == run.id, AgentEvent.event_type == "sandbox.log"
            )
        ).all()
        logs = "".join(event.payload["content"] for event in events)
        assert '"download": 200' in logs and '"http_api": 200' in logs
        assert "Python 3.12.16" in logs and "v24.22.2" in logs
        assert "/workspace/.venv" in logs
        tmp_path.joinpath("online-evidence.json").write_text(
            json.dumps(
                {"logs": logs, "scope": str(scopes[0].id), "workspace": scopes[0].workspace_volume}
            ),
            encoding="utf-8",
        )
        question_id = question.id
    answer(factory, settings, question_id)
    restored = (
        "python -c 'import six; print(six.__version__)' && "
        "node -e \"console.log(require('is-number')(7))\" && "
        "test -s public.html && cat result.csv"
    )

    def complete(body):
        result = tool_result(body, "export")
        assert result["resources"][0]["kind"] == "text"
        assert "minio://image/agent-harness/operation/" in result["resources"][0]["uri"]
        return {"text": "代码环境恢复，已归档 CSV 成果"}

    resumed = RuntimeTransport(
        [
            calls(("restore", "execute", {"command": restored})),
            calls(("export", "runtime__export_artifacts", {"paths": ["result.csv"]})),
            complete,
        ]
    )
    execute_next(configured, accepted, resumed)
    storage = MinioStorage(settings)
    managed = StorageService(storage, settings)
    try:
        with factory() as session:
            assert session.get(AgentRun, accepted.run.id).status == "succeeded"
            scopes = session.scalars(
                select(AgentExecutionScope).where(AgentExecutionScope.run_id == accepted.run.id)
            ).all()
            assert all(scope.sandbox_status == "closed" for scope in scopes)
            exported = session.scalar(
                select(AgentOperation).where(
                    AgentOperation.run_id == accepted.run.id,
                    AgentOperation.tool_name == "runtime__export_artifacts",
                )
            )
            reference = exported.result_refs[0]
        with managed.open(reference["uri"]) as stream:
            assert stream.read() == b"name,value\npython,3.12\nnode,24\n"
        with factory() as session:
            service = task_service(session, settings)
            current = service.get_conversation(conversation.id)
            assert (
                service.delete_global_conversation(
                    conversation.id, {"row_version": current.row_version}, uuid4().hex
                ).status
                == "deleted"
            )
        assert purge_one(factory, managed)
        assert recover(factory, settings) == 0
        from short_drama.core.exceptions import NotFound

        with pytest.raises(NotFound):
            managed.stat(reference["uri"])
        with factory() as session:
            assert session.get(AgentOperation, exported.id).result_refs == []
            assert all(
                not event.payload
                for event in session.scalars(
                    select(AgentEvent).where(
                        AgentEvent.run_id == accepted.run.id,
                        AgentEvent.event_type.like("sandbox.%"),
                    )
                )
            )
    finally:
        managed.delete(reference["uri"]) if "reference" in locals() else None
        storage.close()


def test_sdk_timeout_kills_remote_command_and_stop_cancels_real_execute(
    harness_workspace, tmp_path
):
    from opensandbox import SandboxManagerSync
    from opensandbox.exceptions import SandboxApiException

    from short_drama.agent.runtime.executor import AgentRuntime

    factory, _, model = harness_workspace
    settings = Settings()
    configured = (factory, settings, model)
    _, accepted = new_task(configured)
    _, context = runtime(configured, accepted)
    service = workspace(context)
    try:
        backend = service.backend(context.scope_id, fence=context.fence)
        response = backend.execute("sleep 3; echo timeout-leak > timeout.txt", timeout=1)
        assert response.exit_code != 0
        time.sleep(3)
        assert backend.execute("test ! -e timeout.txt").exit_code == 0
    finally:
        service.cleanup_run(context.run_id, fence=context.fence)
    # 独立 Run 经正式 ToolNode 运行，不把本地 Future 取消当成停止证据。
    with factory() as session:
        task_service(session, settings).control_task(
            accepted.task.id, {"kind": "stop"}, uuid4().hex
        )
    with factory() as session:
        conversation = task_service(session, settings).create_global_conversation({}, uuid4().hex)
        accepted = task_service(session, settings).send_task_message(
            conversation.id, {"content": "执行停止验收", "model_config_id": model}, uuid4().hex
        )
    transport = RuntimeTransport(
        [
            calls(
                (
                    "long",
                    "execute",
                    {
                        "command": (
                            "python -u -c \"import time,pathlib; print('STOP_READY',flush=True); "
                            "time.sleep(20); pathlib.Path('late.txt').write_text('LEAK')\""
                        )
                    },
                )
            )
        ]
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            AgentRuntime(factory, settings, transport=transport).execute, accepted.run.id, 1
        )
        deadline = time.monotonic() + 25
        identifier = None
        while time.monotonic() < deadline:
            with factory() as session:
                ready = session.scalar(
                    select(AgentEvent).where(
                        AgentEvent.run_id == accepted.run.id, AgentEvent.event_type == "sandbox.log"
                    )
                )
                scope = session.scalar(
                    select(AgentExecutionScope).where(AgentExecutionScope.run_id == accepted.run.id)
                )
                if ready and "STOP_READY" in ready.payload["content"]:
                    identifier = scope.sandbox_id
                    break
            time.sleep(0.1)
        assert identifier, "真实远端命令未进入运行状态"
        started = time.monotonic()
        with factory() as session:
            task_service(session, settings).control_task(
                accepted.task.id, {"kind": "stop"}, uuid4().hex
            )
        future.result(timeout=12)
        elapsed = time.monotonic() - started
        assert elapsed < 12
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "cancelled"
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == accepted.run.id)
        ).all()
        assert all(scope.sandbox_status == "closed" for scope in scopes)
    with SandboxManagerSync.create(connection_config=service.connection()) as manager:
        with pytest.raises(SandboxApiException) as raised:
            manager.get_sandbox_info(identifier)
        assert raised.value.status_code == 404
    tmp_path.joinpath("stop-evidence.json").write_text(
        json.dumps(
            {"sandbox_id": identifier, "seconds_after_stop": elapsed, "remote_absent": True}
        ),
        encoding="utf-8",
    )


def test_real_resource_tools_skill_idempotency_conflicts_authorization_and_log_archive(
    harness_workspace, tmp_path
):
    from short_drama.agent.runtime.executor import AgentRuntime
    from short_drama.agent.tools.registry import get_spec
    from short_drama.service.media.files.storage import StorageService
    from short_drama.service.skills.catalog.skill import AgentSkillService
    from short_drama.storage.minio import MinioStorage
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    factory, _, model = harness_workspace
    settings = Settings()
    configured = (factory, settings, model)
    storage = MinioStorage(settings)
    managed = StorageService(storage, settings)
    skills = []
    for index in (1, 2):
        with factory() as session:
            session.info["actor"] = task_actor()
            skills.append(
                AgentSkillService(session, settings, storage, tool_lookup=get_spec).create(
                    {
                        "name": f"沙箱方法{index}",
                        "slug": f"sandbox-method-{index}",
                        "description": "真实代码资源交接",
                        "instructions": "显式物化后执行脚本，输出写工作目录。",
                        "resources": [
                            {
                                "path": "scripts/check.py",
                                "kind": "script",
                                "content": f"print('SKILL_{index}')\n",
                            }
                        ],
                    },
                    uuid4().hex,
                )
            )
    with factory() as session:
        from short_drama.domain.skills.catalog.skill import AgentSkillVersion

        source_locators = [
            f"minio://{item['bucket']}/{item['object_name']}"
            for version in session.scalars(select(AgentSkillVersion))
            for item in version.resources
        ]
    conversation, accepted = new_task(configured)
    captured = {}

    def materialize(body):
        captured["activations"] = [
            tool_result(body, key)["activation_id"] for key in ("load-1", "load-2")
        ]
        return calls(
            *[
                (
                    f"materialize-{index}",
                    "runtime__materialize_skill_resource",
                    {"activation_id": activation, "path": "scripts/check.py"},
                )
                for index, activation in enumerate(captured["activations"], 1)
            ]
        )

    def repeat(body):
        captured["paths"] = [
            tool_result(body, key)["path"] for key in ("materialize-1", "materialize-2")
        ]
        assert len(set(captured["paths"])) == 2
        return calls(
            (
                "repeat",
                "runtime__materialize_skill_resource",
                {"activation_id": captured["activations"][0], "path": "scripts/check.py"},
            )
        )

    def execute_resources(body):
        assert tool_result(body, "repeat")["path"] == captured["paths"][0]
        command = (
            "python "
            + shlex.quote(captured["paths"][0])
            + " && python "
            + shlex.quote(captured["paths"][1])
            + " && python -c "
            + shlex.quote(
                "from pathlib import Path; "
                f"Path({captured['paths'][0]!r}).write_text('tampered'); "
                "Path('output.json').write_text('{\"ok\":true}'); "
                "Path('binary.bin').write_bytes(bytes([0,255])); "
                "Path('escape').symlink_to('/etc/passwd'); print('X'*70000)"
            )
        )
        return calls(("run-script", "execute", {"command": command}))

    def conflicts(_body):
        return calls(
            (
                "conflict",
                "runtime__materialize_skill_resource",
                {"activation_id": captured["activations"][0], "path": "scripts/check.py"},
            ),
            ("escape", "runtime__export_artifacts", {"paths": ["escape"]}),
            ("binary-read", "read_file", {"file_path": "/workspace/binary.bin"}),
            ("export", "runtime__export_artifacts", {"paths": ["output.json", "binary.bin"]}),
        )

    def import_again(body):
        assert tool_result(body, "conflict")["error"]
        assert tool_result(body, "escape")["error"]["code"] == "agent_sandbox_path"
        assert "二进制" in next(
            item["content"]
            for item in body["messages"]
            if item.get("tool_call_id") == "binary-read"
        )
        captured["references"] = tool_result(body, "export")["resources"]
        assert {reference["kind"] for reference in captured["references"]} == {"text", "file"}
        forged = {**captured["references"][0], "uri": "minio://image/foreign/secret.json"}
        return calls(
            (
                "import",
                "runtime__import_resources",
                {"resources": [{"resource": captured["references"][0], "path": "copy.json"}]},
            ),
            (
                "forged",
                "runtime__import_resources",
                {"resources": [{"resource": forged, "path": "forged.json"}]},
            ),
        )

    def release(body):
        assert tool_result(body, "import")["imports"][0]["path"] == "/workspace/copy.json"
        assert tool_result(body, "forged")["error"]
        return calls(
            ("release", "runtime__release_skill", {"activation_id": captured["activations"][0]}),
            (
                "verify-copy",
                "execute",
                {"command": "cmp output.json copy.json && test ! -e forged.json"},
            ),
        )

    def disallowed(_body):
        return calls(
            (
                "released",
                "runtime__materialize_skill_resource",
                {"activation_id": captured["activations"][0], "path": "scripts/check.py"},
            )
        )

    def done(body):
        assert tool_result(body, "released")["error"]
        return {"text": "资源导入导出、物化与冲突边界验证完成"}

    transport = RuntimeTransport(
        [
            calls(
                *[
                    (
                        f"load-{index}",
                        "runtime__load_skill",
                        {
                            "name": skill.name,
                            "content_version": str(skill.content_version),
                            "purpose": "运行脚本",
                        },
                    )
                    for index, skill in enumerate(skills, 1)
                ]
            ),
            materialize,
            repeat,
            execute_resources,
            conflicts,
            import_again,
            release,
            disallowed,
            done,
        ]
    )
    locators = []
    try:
        AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, 1)
        with factory() as session:
            assert session.get(AgentRun, accepted.run.id).status == "succeeded"
            operation = session.scalar(
                select(AgentOperation)
                .where(
                    AgentOperation.run_id == accepted.run.id, AgentOperation.tool_name == "execute"
                )
                .order_by(AgentOperation.id)
            )
            log = next(ref for ref in operation.result_refs if ref["source"] == "sandbox_log")
            assert log["metadata"]["byte_size"] > 65536
            locators = [log["uri"], *[ref["uri"] for ref in captured["references"]]]
            logs = session.scalars(
                select(AgentEvent).where(
                    AgentEvent.run_id == accepted.run.id,
                    AgentEvent.event_type == "sandbox.log",
                    AgentEvent.payload["operation_id"].as_string() == str(operation.id),
                )
            ).all()
            assert sum(len(row.payload["content"].encode()) for row in logs) <= 65536
        with managed.open(log["uri"]) as stream:
            assert b"SKILL_1" in stream.read()
        tmp_path.joinpath("resource-evidence.json").write_text(
            json.dumps(
                {"paths": captured["paths"], "references": captured["references"], "log": log}
            ),
            encoding="utf-8",
        )
        with factory() as session:
            service = task_service(session, settings)
            current = service.get_conversation(conversation.id)
            service.delete_global_conversation(
                conversation.id, {"row_version": current.row_version}, uuid4().hex
            )
        from short_drama.agent.session.purge import purge_one

        assert purge_one(factory, managed)
        from short_drama.core.exceptions import NotFound

        for locator in locators:
            with pytest.raises(NotFound):
                managed.stat(locator)
    finally:
        for locator in [*locators, *source_locators]:
            managed.delete(locator)
        storage.close()


def test_native_task_isolates_real_workspaces_and_restores_explicit_file_handoff(
    harness_workspace, tmp_path
):
    from short_drama.agent.runtime.executor import AgentRuntime
    from short_drama.service.media.files.storage import StorageService
    from short_drama.storage.minio import MinioStorage
    from tests.integration.standard.assistant.test_agent_questions import (
        QUESTION,
        answer,
        execute_next,
    )
    from tests.integration.standard.assistant.test_agent_subtasks import (
        SubtaskTransport,
        child_name,
        delegation,
    )

    factory, _, model = harness_workspace
    settings = Settings()
    configured = (factory, settings, model)
    _, accepted = new_task(configured)
    references = {}

    def executed(body, identifier):
        content = next(
            item["content"] for item in body["messages"] if item.get("tool_call_id") == identifier
        )
        assert "[Command succeeded with exit code 0]" in content

    def parent(body, number):
        if number == 1:
            return calls(("root-write", "execute", {"command": "printf root > parent.csv"}))
        if number == 2:
            executed(body, "root-write")
            return calls(("root-export", "runtime__export_artifacts", {"paths": ["parent.csv"]}))
        references["parent"] = tool_result(body, "root-export")["resources"][0]
        return calls(
            delegation("editor", {"resources": [references["parent"]]}),
            delegation("stranger"),
        )

    def child(name, body):
        last = body["messages"][-1]
        if name == "stranger":
            if last["role"] != "tool":
                return calls(
                    (
                        "unshared",
                        "runtime__import_resources",
                        {"resources": [{"resource": references["parent"], "path": "copy.csv"}]},
                    )
                )
            assert tool_result(body, "unshared")["error"]
            return {"text": "未交接文件不可导入"}
        if last["role"] != "tool":
            return calls(("child-empty", "execute", {"command": "test ! -e parent.csv"}))
        identifier = last["tool_call_id"]
        if identifier == "child-empty":
            executed(body, identifier)
            return calls(
                (
                    "child-import",
                    "runtime__import_resources",
                    {"resources": [{"resource": references["parent"], "path": "input.csv"}]},
                )
            )
        if identifier == "child-import":
            assert tool_result(body, identifier)["imports"][0]["path"] == "/workspace/input.csv"
            return calls(
                (
                    "child-edit",
                    "execute",
                    {"command": 'test "$(cat input.csv)" = root && printf child > input.csv'},
                )
            )
        if identifier == "child-edit":
            executed(body, identifier)
            return calls(("child-export", "runtime__export_artifacts", {"paths": ["input.csv"]}))
        if identifier == "child-export":
            references["child"] = tool_result(body, identifier)["resources"][0]
            return calls(("question", "runtime__ask_user", QUESTION))
        if identifier == "question":
            return calls(
                (
                    "child-restored",
                    "execute",
                    {"command": 'test "$(cat input.csv)" = child && test ! -e parent.csv'},
                )
            )
        assert identifier == "child-restored"
        executed(body, identifier)
        return {"text": "子副本已恢复并归档"}

    first = SubtaskTransport([], parent_reply=parent, child_reply=child)
    AgentRuntime(factory, settings, transport=first).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "waiting_input" and run.lease_token is None, run.error
        scopes = session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.run_id == run.id)
        ).all()
        files = {scope.id: scope.workspace_volume for scope in scopes if scope.workspace_volume}
        assert len(files) == len(set(files.values())) == 2
        assert all(scope.sandbox_id is None for scope in scopes)
        question = session.scalar(
            select(AgentOperation).where(
                AgentOperation.run_id == run.id, AgentOperation.tool_name == "runtime__ask_user"
            )
        )
        question_id, child_scope = question.id, question.scope_id
        namespace = question.wait_state["namespace"]
    answer(factory, settings, question_id)

    def finish_parent(body, number):
        if number == 1:
            result = tool_result(body, "editor")
            exported = [ref for ref in result["result_refs"] if ref["source"] == "sandbox_artifact"]
            assert exported == [references["child"]] and result["scope_id"] == str(child_scope)
            return calls(
                (
                    "root-import",
                    "runtime__import_resources",
                    {"resources": [{"resource": exported[0], "path": "returned.csv"}]},
                )
            )
        if number == 2:
            assert (
                tool_result(body, "root-import")["imports"][0]["path"] == "/workspace/returned.csv"
            )
            return calls(
                (
                    "root-check",
                    "execute",
                    {
                        "command": 'test "$(cat parent.csv)" = root && '
                        'test "$(cat returned.csv)" = child'
                    },
                )
            )
        executed(body, "root-check")
        return {"text": "父文件完整，已接收子任务归档副本"}

    second = SubtaskTransport([], parent_reply=finish_parent, child_reply=child)
    execute_next(configured, accepted, second)
    assert [child_name(body) for body in second.main_requests[:2]] == ["editor", "editor"]
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        question = session.get(AgentOperation, question_id)
        assert question.scope_id == child_scope and question.wait_state["namespace"] == namespace
        assert question.wait_state["consumed"]
        scopes = session.scalars(select(AgentExecutionScope)).all()
        assert len(scopes) == 3
        assert all(
            scope.sandbox_status == ("closed" if scope.id in files else None) for scope in scopes
        )
    storage = MinioStorage(settings)
    try:
        managed = StorageService(storage, settings)
        for name, expected in (("parent", b"root"), ("child", b"child")):
            with managed.open(references[name]["uri"]) as stream:
                assert stream.read() == expected
    finally:
        storage.close()
    tmp_path.joinpath("native-task-files-evidence.json").write_text(
        json.dumps({"workspaces": files, "child_scope": str(child_scope), "namespace": namespace}),
        encoding="utf-8",
    )
