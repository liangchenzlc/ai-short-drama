"""本人本地任务媒体回收：真实 HTTP/MySQL/MinIO，终态受控，不代替 Worker 验收。"""

import os
import wave
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from short_drama.core.exceptions import NotFound
from short_drama.dao.canvas.resources.recycle.recycle_cleanup import CanvasRecycleCleanupDAO
from short_drama.domain import (
    CanvasLocalJob,
    CanvasRenderJob,
    CanvasResourceDeletion,
    CanvasResourceUpload,
    MediaFile,
    ProjectCanvas,
)
from short_drama.domain.canvas.document.canvas import CanvasWriteReceipt
from short_drama.service.base import utcnow
from short_drama.service.canvas.timeline.rendering.render import CanvasRenderService
from short_drama.service.canvas.tools.local.local import CanvasLocalService
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id
from tests.integration.canvas.depth.test_canvas_depth_execution import (
    depth_runtime as depth_runtime,
)
from tests.integration.canvas.generation.test_canvas_generation_runtime import seed
from tests.integration.canvas.resources.test_canvas_library_deletion import register
from tests.integration.canvas.resources.test_canvas_media_locators import save
from tests.integration.canvas.resources.test_canvas_recycle_cleanup import (
    cleanup,
    dispose,
    remove_bytes,
)
from tests.integration.canvas.resources.test_canvas_recycle_execution_cleanup import (
    keep_project_active,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.canvas.resources.test_canvas_resources import upload
from tests.integration.canvas.timeline.test_canvas_render_execution import runtime as runtime
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="本地任务媒体回收需显式启用隔离 MinIO",
    ),
]
PATH = "/api/v1/canvas-runtime"


def wav():
    content = BytesIO()
    with wave.open(content, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\0\0" * 2400)
    return content.getvalue()


@pytest.fixture
def local_media(resource_app):
    owner, user, project, path, request = seed(resource_app, "local_disposal_case")
    uploaded = upload(
        owner, wav(), source=request["projectId"], kind="audio", name="input.wav", mime="audio/wav"
    )
    assert uploaded.status_code == 200, uploaded.text
    return SimpleNamespace(
        owner=owner,
        user=user,
        project=project,
        path=path,
        source=uploaded.json()["resource"],
        body={
            "projectId": request["projectId"],
            "resourceId": uploaded.json()["resource"]["id"],
            "language": "zh",
            "clientOperationId": "disposed-local-operation",
        },
    )


def finish_local(app, identifier):
    with app[1].begin() as session:
        job = session.get(CanvasLocalJob, int(identifier))
        job.status, job.finished_at = "failed", utcnow()
        job.error_json = {"code": "fixture_failed", "message": "受控终态"}


@pytest.fixture
def local_case(resource_app, local_media):
    case = local_media
    response = case.owner.post(PATH + "/timeline/transcriptions", json=case.body)
    assert response.status_code == 202, response.text
    case.task = response.json()
    finish_local(resource_app, case.task["id"])
    return case


def dispose_case(app, case):
    keep_project_active(case.owner, case.project)
    document = case.owner.get(case.path + "/my-document").json()["source_document"]
    dispose(app, case.owner, case.path, document)


def test_concurrent_first_admission_replays_the_original_operation(resource_app, local_media):
    barrier = Barrier(2)

    def submit(_index):
        barrier.wait(timeout=10)
        return local_media.owner.post(PATH + "/timeline/transcriptions", json=local_media.body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 202], [
        response.text for response in responses
    ]
    assert responses[0].json() == responses[1].json()
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasLocalJob)) == 1


@pytest.mark.parametrize("protection", ["other_job", "library"])
def test_disposal_releases_only_its_job_and_keeps_independent_references(
    resource_app, local_case, protection
):
    case = local_case
    if protection == "other_job":
        document = case.owner.get(case.path + "/my-document").json()["source_document"]
        document["nodes"].append(
            {
                "id": "shared-audio",
                "type": "audio",
                "title": "独立任务引用的音频",
                "position": {"x": 0, "y": 0},
                "width": 320,
                "height": 220,
                "metadata": {"storageKey": "resource:" + case.source["id"]},
            }
        )
        save(case.owner, case.path, document, "publish-disposal-audio")
        sibling = case.owner.post(
            f"/api/v1/projects/{case.project['id']}/canvases",
            json={"title": "独立任务", "source_key": "independent-local-canvas"},
            headers={"Idempotency-Key": "independent-local-canvas"},
        )
        assert sibling.status_code == 201, sibling.text
        second_body = {
            **case.body,
            "projectId": sibling.json()["source_key"],
            "clientOperationId": "independent-local-job",
        }
        second = case.owner.post(PATH + "/timeline/transcriptions", json=second_body)
        assert second.status_code == 202, second.text
        finish_local(resource_app, second.json()["id"])
        with resource_app[1]() as session:
            other = session.get(CanvasLocalJob, int(second.json()["id"]))
            frozen = (
                deepcopy(other.request_json),
                deepcopy(other.source_snapshot),
                other.request_hash,
            )
    else:
        register(case.owner, "local-kept-audio", case.source)
    dispose_case(resource_app, case)
    result = cleanup(resource_app)
    assert result["completed"] == 1 and result["failed"] == 0
    assert result["retired_uploads"] == 0 and result["retained_uploads"] == 1
    with resource_app[1]() as session:
        job = session.get(CanvasLocalJob, int(case.task["id"]))
        assert job.source_media_id is None and job.disposal_receipt_id is not None
        assert session.get(MediaFile, int(case.source["id"])) is not None
        if protection == "other_job":
            other = session.get(CanvasLocalJob, int(second.json()["id"]))
            assert (other.request_json, other.source_snapshot, other.request_hash) == frozen
            assert other.source_media_id == int(case.source["id"])
            assert (
                other.disposal_receipt_id is None and other.error_json["code"] == "fixture_failed"
            )
    assert remove_bytes(resource_app)["completed"] == 0
    assert case.owner.get(PATH + f"/resources/{case.source['id']}/file").content == wav()


def test_failed_cleanup_commit_rolls_back_local_payload_and_upload_retirement(
    resource_app, local_case
):
    case = local_case
    dispose_case(resource_app, case)

    def fail_commit(_session):
        raise RuntimeError("Injected local disposal commit failure")

    event.listen(resource_app[1].class_, "before_commit", fail_commit)
    try:
        result = cleanup(resource_app)
    finally:
        event.remove(resource_app[1].class_, "before_commit", fail_commit)
    assert result["failed"] == 1 and result["completed"] == result["retired_uploads"] == 0
    with resource_app[1]() as session:
        job = session.get(CanvasLocalJob, int(case.task["id"]))
        assert job.disposal_receipt_id is None and job.source_media_id == int(case.source["id"])
        assert job.request_json == case.body and job.source_snapshot
        assert job.error_json["code"] == "fixture_failed"
        assert session.get(MediaFile, int(case.source["id"])) is not None
        assert session.scalar(select(func.count()).select_from(CanvasResourceDeletion)) == 0
    assert cleanup(resource_app)["retired_uploads"] == 1


def test_old_v2_marker_still_releases_its_remaining_local_source_pin(resource_app, local_case):
    case = local_case
    dispose_case(resource_app, case)
    with resource_app[1].begin() as session:
        canvas = session.get(ProjectCanvas, int(case.project["primary_canvas_id"]))
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.operation_kind == "canvas.recycle.purge"
            )
        )
        CanvasRecycleCleanupDAO(session).remove_payload(canvas.id)
        job = session.get(CanvasLocalJob, int(case.task["id"]))
        job.request_json = job.source_snapshot = {}
        job.error_json = {"code": "canvas_work_disposed", "message": "旧版已处置"}
        canvas.properties_json = CanvasRecycleCleanupDAO.marker(receipt.id)
    result = cleanup(resource_app)
    assert result["completed"] == result["retired_uploads"] == 1 and result["failed"] == 0
    with resource_app[1]() as session:
        job = session.get(CanvasLocalJob, int(case.task["id"]))
        assert job.disposal_receipt_id == receipt.id and job.source_media_id is None
        assert session.get(MediaFile, int(case.source["id"])) is None
    assert cleanup(resource_app)["examined"] == 0


def test_concurrent_cleanup_retires_local_source_once(resource_app, local_case, monkeypatch):
    dispose_case(resource_app, local_case)
    barrier, original = Barrier(2), CanvasRecycleCleanupDAO.candidates

    def synchronize(dao, after_id):
        value = original(dao, after_id)
        if after_id == 0:
            barrier.wait(timeout=10)
        return value

    with monkeypatch.context() as fault:
        fault.setattr(CanvasRecycleCleanupDAO, "candidates", synchronize)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: cleanup(resource_app), range(2)))
    assert sum(item["completed"] for item in results) == 1
    assert sum(item["retired_uploads"] for item in results) == 1
    assert sum(item["failed"] for item in results) == 0
    with resource_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasResourceDeletion)) == 1
    assert remove_bytes(resource_app)["completed"] == 1


@pytest.mark.parametrize("invalid", ["source", "depth_output", "render_output", "active_disposal"])
def test_ordinary_local_jobs_still_require_their_source_output_and_valid_terminal_state(
    resource_app, local_case, invalid
):
    case = local_case
    if invalid == "active_disposal":
        dispose_case(resource_app, case)
    expected = {
        "source": "ck_canvas_local_source",
        "depth_output": "ck_canvas_local_output",
        "render_output": "ck_canvas_render_output",
        "active_disposal": "ck_canvas_local_disposal",
    }[invalid]
    with (
        pytest.raises(OperationalError, match=expected) as failure,
        resource_app[1].begin() as session,
    ):
        job = session.get(CanvasLocalJob, int(case.task["id"]))
        if invalid == "source":
            job.source_media_id = None
        elif invalid == "depth_output":
            job.task_type, job.status, job.result_json = "depth_capture", "succeeded", {}
        elif invalid == "active_disposal":
            receipt = session.scalar(
                select(CanvasWriteReceipt).where(
                    CanvasWriteReceipt.operation_kind == "canvas.recycle.purge"
                )
            )
            job.disposal_receipt_id = receipt.id
            job.status, job.finished_at = "queued", None
            job.request_json = job.source_snapshot = {}
            job.error_json = {"code": "canvas_work_disposed"}
        else:
            session.add(
                CanvasRenderJob(
                    id=next_id(),
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    created_by=job.user_id,
                    updated_by=job.user_id,
                    user_id=job.user_id,
                    project_id=job.project_id,
                    canvas_id=job.canvas_id,
                    canvas_version=1,
                    client_operation_id="missing-render-output",
                    request_hash="3" * 64,
                    request_json={},
                    plan_json={},
                    source_snapshot={},
                    status="succeeded",
                    result_json={},
                    finished_at=utcnow(),
                    next_run_at=utcnow(),
                )
            )
        session.flush()
    assert failure.value.orig.args[0] == 3819


def native_disposal(app, owner, project, task, source_id, executor, model, service, protection):
    with app[1]() as session:
        job = session.get(model, int(task["id"]))
        assert job.status == "succeeded"
        output_id = job.output_media_id
        frozen = (
            job.id,
            job.request_hash,
            job.client_operation_id,
            job.message_version,
            job.attempts,
        )
        locators = {
            item.id: item.storage_locator
            for item in session.scalars(
                select(MediaFile).where(MediaFile.id.in_([source_id, output_id]))
            )
        }
        upload = session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.media_id == output_id)
        )
        assert upload.mode == "chunked" and upload.status == "ready"
    if protection == "library":
        resource = owner.get(PATH + f"/resources/{output_id}").json()["resource"]
        register(owner, "kept-local-output", resource)
    keep_project_active(owner, project)
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    document = owner.get(path + "/my-document").json()["source_document"]
    dispose(app, owner, path, document)
    result = cleanup(app)
    expected = 1 if protection == "library" else 2
    assert result["completed"] == 1 and result["failed"] == 0
    assert result["retired_uploads"] == expected and result["retained_uploads"] == 2 - expected
    with app[1]() as session:
        job = session.get(model, int(task["id"]))
        assert (
            job.id,
            job.request_hash,
            job.client_operation_id,
            job.message_version,
            job.attempts,
        ) == frozen
        assert job.output_media_id is None and job.disposal_receipt_id is not None
        assert job.request_json == job.source_snapshot == job.result_json == {}
        if model is CanvasLocalJob:
            assert job.source_media_id is None
        else:
            assert job.plan_json == {}
        canvas = session.get(ProjectCanvas, job.canvas_id)
        projection = service(session, app[2])._project(job, canvas)
        assert projection["resultState"] == "NOT_AVAILABLE"
        assert not projection["canRetry"] and not projection["canResume"]
        assert "resultJson" not in projection
        assert session.get(MediaFile, source_id) is None
        assert (session.get(MediaFile, output_id) is not None) == (protection == "library")
    assert remove_bytes(app)["completed"] == expected
    for identifier, locator in locators.items():
        location = ObjectLocation.parse(locator, {app[2].minio_video_bucket})
        if identifier == output_id and protection == "library":
            assert app[0].state.storage.stat(location.bucket, location.object_name).size > 0
        else:
            with pytest.raises(NotFound):
                app[0].state.storage.stat(location.bucket, location.object_name)
    assert owner.get(PATH + f"/tasks/{task['id']}").status_code == 404
    assert owner.post(PATH + f"/tasks/{task['id']}/resume").status_code == 404
    executor.execute(task["id"], str(frozen[3]))
    assert cleanup(app)["examined"] == 0


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_TIMELINE_FFMPEG") != "1", reason="需显式启用真实 FFmpeg"
)
@pytest.mark.parametrize("protection", ["none", "library"])
def test_successful_render_releases_frozen_sources_and_retires_its_original_archive(
    runtime, protection
):
    owner, _, project, source, task, executor, app = runtime
    executor.execute(task["id"], "1")
    native_disposal(
        app,
        owner,
        project,
        task,
        int(source["id"]),
        executor,
        CanvasRenderJob,
        CanvasRenderService,
        protection,
    )


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_DEPTH_NATIVE") != "1", reason="需显式启用深度组件替身/FFmpeg"
)
@pytest.mark.parametrize("protection", ["none", "library"])
def test_successful_depth_releases_source_and_retires_the_original_chunked_archive(
    depth_runtime, protection
):
    case = depth_runtime
    response = case.owner.post(PATH + "/depth-captures", json=case.body)
    assert response.status_code == 202, response.text
    task = response.json()
    case.executor.execute(task["id"], "1")
    native_disposal(
        case.app,
        case.owner,
        case.project,
        task,
        int(case.source["id"]),
        case.executor,
        CanvasLocalJob,
        CanvasLocalService,
        protection,
    )
    replay = case.owner.post(PATH + "/depth-captures", json=case.body)
    assert replay.status_code == 404, replay.text
    with case.app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasLocalJob)) == 1


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_disposed_local_input_releases_its_pin_and_retires_the_original_upload(
    resource_app, status
):
    client, user, project, path, request = seed(resource_app, "local_disposal_" + status)
    uploaded = upload(
        client, wav(), source=request["projectId"], kind="audio", name="input.wav", mime="audio/wav"
    )
    assert uploaded.status_code == 200, uploaded.text
    identifier = int(uploaded.json()["resource"]["id"])
    now, job_id = utcnow(), next_id()
    with resource_app[1].begin() as session:
        source = session.get(MediaFile, identifier)
        session.add(
            CanvasLocalJob(
                id=job_id,
                created_at=now,
                updated_at=now,
                created_by=int(user["id"]),
                updated_by=int(user["id"]),
                user_id=int(user["id"]),
                project_id=int(project["id"]),
                canvas_id=int(project["primary_canvas_id"]),
                task_type="timeline_transcription",
                client_operation_id="disposed-local-input",
                request_hash="5" * 64,
                request_json={"resourceId": str(identifier)},
                source_media_id=identifier,
                source_snapshot={"media_id": str(identifier), "locator": source.storage_locator},
                canvas_version=1,
                status=status,
                next_run_at=now,
                finished_at=now,
                result_json={"text": "本人转写正文"} if status == "succeeded" else None,
            )
        )
    document = client.get(path + "/my-document").json()["source_document"]
    dispose(resource_app, client, path, document)
    result = cleanup(resource_app)
    assert result["completed"] == 1 and result["failed"] == 0
    with resource_app[1]() as session:
        job = session.get(CanvasLocalJob, job_id)
        assert job.source_media_id is None, "已处置本地任务仍永久保留来源外键"
        assert job.status == status and job.request_hash == "5" * 64
        assert job.request_json == job.source_snapshot == {}
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.canvas_id == int(project["primary_canvas_id"]),
                CanvasWriteReceipt.operation_kind == "canvas.recycle.purge",
            )
        )
        assert job.disposal_receipt_id == receipt.id
        assert session.get(MediaFile, identifier) is None
    assert result["retired_uploads"] == 1 and result["retained_uploads"] == 0
    assert remove_bytes(resource_app)["completed"] == 1
    with pytest.raises(NotFound):
        resource_app[0].state.storage.stat(
            resource_app[2].minio_audio_bucket, f"canvas/resources/{identifier}"
        )
