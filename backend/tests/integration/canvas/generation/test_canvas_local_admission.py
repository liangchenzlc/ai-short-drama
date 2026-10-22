"""真实 MySQL/登录的本地任务准入；来源仅为 metadata fixture，不执行模型。"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from short_drama.core.exceptions import WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db.canvas_local_access import canvas_local_write
from short_drama.domain import AsyncTask, CanvasLocalJob, MediaFile
from short_drama.service.base import utcnow
from tests.integration.canvas.timeline.test_canvas_render_admission import add_media
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = pytest.mark.integration
PATH = "/api/v1/canvas-runtime"


@pytest.fixture
def local_scope(identity_app):
    owner, user = account(identity_app, "local_admission_owner")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "local-admission-project"},
        json={"name": "本地转写准入", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    document = owner.get(
        f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}/my-document"
    ).json()["source_document"]
    media = add_media(identity_app, project["id"], user["id"])
    body = {
        "projectId": document["id"],
        "resourceId": media,
        "language": " zh ",
        "clientOperationId": "local-admission-operation",
    }
    return owner, user, project, body


def create(owner, body):
    response = owner.post(PATH + "/timeline/transcriptions", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def test_cloud_model_freeze_operation_replay_and_unknown_submission_cannot_resume(
    identity_app, local_scope
):
    owner, _, _, body = local_scope
    model = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "service_type": "audio",
            "name": "转写测试",
            "provider": "openai",
            "model_key": "transcribe-fixture",
            "base_url": "https://transcription.invalid/v1",
            "apikey": "transcription-fixture-placeholder",
            "runtime_profile": {
                "version": 1,
                "api_format": "openai",
                "protocol": "openai-audio",
                "capability_config": {"audio": {"transcriptionSupported": True}},
            },
        },
    )
    assert model.status_code == 201, model.text
    model = model.json()
    choices = owner.get(PATH + "/timeline/transcription-models")
    assert choices.status_code == 200 and choices.json()["models"] == [
        {"id": model["id"], "displayName": model["name"]}
    ]
    body = {**body, "model_config_id": model["id"]}
    task = create(owner, body)
    assert task["provider"] == "cloud" and task["model"] == "transcribe-fixture"
    changed = owner.patch(
        "/api/v1/ai-model-configs/" + model["id"],
        json={"row_version": model["row_version"], "enabled": False},
    )
    assert changed.status_code == 200, changed.text
    replay = owner.post(PATH + "/timeline/transcriptions", json=body)
    assert replay.status_code == 200 and replay.json() == task
    assert (
        owner.post(
            PATH + "/timeline/transcriptions",
            json={**body, "clientOperationId": "new-disabled-model-operation"},
        ).status_code
        == 422
    )
    with identity_app[1].begin() as session:
        job = session.get(CanvasLocalJob, int(task["id"]))
        job.status, job.stage, job.finished_at = (
            "failed",
            "提交结果待核实",
            utcnow(),
        )
        job.result_json = {"cloud_submission_started": True}
        job.error_json = {"code": "canvas_transcription_submission_unknown", "message": "待核实"}
    unknown = owner.get(PATH + f"/tasks/{task['id']}").json()
    assert not unknown["canRetry"] and not unknown["canResume"]
    blocked = owner.post(PATH + f"/tasks/{task['id']}/resume")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "canvas_transcription_submission_unknown"


def test_transcription_create_replay_frozen_source_and_private_task_center(
    identity_app, local_scope
):
    owner, user, project, body = local_scope
    task = create(owner, body)
    assert task["type"] == "timeline_transcription" and task["model"] == "whisper.cpp"
    assert task["status"] == "queued" and task["provider"] == "local"
    assert isinstance(task["id"], str) and int(task["id"]) > 2**53
    assert json.loads(task["inputJson"])["language"] == "zh"
    replay = owner.post(PATH + "/timeline/transcriptions", json=body)
    assert replay.status_code == 200 and replay.json() == task
    assert owner.get(PATH + f"/tasks/{task['id']}").json() == task
    assert [
        item["id"]
        for item in owner.get(PATH + "/tasks", params={"projectId": body["projectId"]}).json()
    ] == [task["id"]]
    assert owner.get(PATH + f"/tasks/{task['id']}/logs").json()[0]["level"] == "info"
    with identity_app[1]() as session:
        job = session.get(CanvasLocalJob, int(task["id"]))
        assert (job.user_id, job.project_id, job.canvas_id, job.source_media_id) == (
            int(user["id"]),
            int(project["id"]),
            int(project["primary_canvas_id"]),
            int(body["resourceId"]),
        )
        assert job.source_snapshot["checksum"] == "a" * 64 and job.source_snapshot["size"] == 1024
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_missing_or_conflicting_operation_and_extra_transport_are_rejected(
    identity_app, local_scope
):
    owner, _, _, body = local_scope
    create(owner, body)
    changed = {**body, "language": "en"}
    assert owner.post(PATH + "/timeline/transcriptions", json=changed).status_code == 409
    assert (
        owner.post(
            PATH + "/timeline/transcriptions",
            json=body,
            headers={"Idempotency-Key": "different-operation"},
        ).status_code
        == 409
    )
    for patch in (
        {"clientOperationId": "bad"},
        {"baseUrl": "http://untrusted.invalid"},
        {"resourceId": "https://untrusted.invalid/audio"},
    ):
        assert (
            owner.post(PATH + "/timeline/transcriptions", json={**body, **patch}).status_code == 422
        )
    assert (
        owner.post(
            PATH + "/timeline/transcriptions",
            json={key: value for key, value in body.items() if key != "clientOperationId"},
        ).status_code
        == 422
    )
    with identity_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasLocalJob)) == 1


def test_only_the_owner_can_create_read_or_cancel_tasks(identity_app, local_scope):
    owner, _, project, body = local_scope
    member, member_user = account(identity_app, "local_admission_member")
    outsider, _ = account(identity_app, "local_admission_outsider")
    first = create(owner, body)
    assert member.post(PATH + "/timeline/transcriptions", json=body).status_code == 404
    for client, other in ((member, first), (outsider, first)):
        for suffix in ("", "/logs"):
            assert client.get(PATH + f"/tasks/{other['id']}" + suffix).status_code == 404
        assert client.post(PATH + f"/tasks/{other['id']}/cancel").status_code == 404
    assert outsider.post(PATH + "/timeline/transcriptions", json=body).status_code == 404
    assert member.get(PATH + "/tasks").json() == []
    assert [item["id"] for item in owner.get(PATH + "/tasks").json()] == [first["id"]]


@pytest.mark.parametrize("source", ["missing", "private", "cross_project", "image"])
def test_unauthorized_or_non_media_source_cannot_insert(identity_app, local_scope, source):
    owner, user, project, body = local_scope
    if source == "missing":
        body["resourceId"] = "1"
    elif source == "private":
        _, other = account(identity_app, "local_source_other")
        body["resourceId"] = add_media(identity_app, project["id"], other["id"], published=False)
    elif source == "cross_project":
        other = owner.post(
            "/api/v1/projects",
            json={"name": "另一画布", "workspace_mode": "infinite_canvas"},
            headers={"Idempotency-Key": "local-other-project"},
        ).json()
        body["resourceId"] = add_media(identity_app, other["id"], user["id"])
    else:
        with identity_app[1].begin() as session:
            from short_drama.domain import MediaFile

            media = session.get(MediaFile, int(body["resourceId"]))
            media.format_code, media.duration_ms = "image/png", None
    response = owner.post(PATH + "/timeline/transcriptions", json=body)
    assert response.status_code in {400, 404}, response.text
    with identity_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasLocalJob)) == 0


def test_omitted_canvas_uses_authorized_source_primary_canvas(local_scope):
    owner, _, _, body = local_scope
    response = create(owner, {key: value for key, value in body.items() if key != "projectId"})
    assert response["projectId"] == body["projectId"]


def test_global_private_source_needs_no_canvas_and_remains_author_only(identity_app, local_scope):
    owner, user, _, body = local_scope
    with identity_app[1].begin() as session:
        media = session.get(MediaFile, int(body["resourceId"]))
        media.project_id, media.scope_user_id, media.published_at = None, int(user["id"]), None
    body = {key: value for key, value in body.items() if key != "projectId"}
    task = create(owner, body)
    assert "projectId" not in task
    assert owner.post(PATH + "/timeline/transcriptions", json=body).json() == task
    other, _ = account(identity_app, "local_global_other")
    assert other.post(PATH + "/timeline/transcriptions", json=body).status_code == 404
    assert other.get(PATH + f"/tasks/{task['id']}").status_code == 404
    assert other.get(PATH + "/tasks").json() == []
    with identity_app[1]() as session:
        job = session.get(CanvasLocalJob, int(task["id"]))
        assert job.project_id is None and job.canvas_id is None and job.canvas_version == 0
        assert job.source_media_id == int(body["resourceId"])
        assert session.scalar(select(func.count()).select_from(CanvasLocalJob)) == 1


def test_cancel_and_explicit_resume_keep_same_source_and_operation(identity_app, local_scope):
    owner, _, _, body = local_scope
    task = create(owner, body)
    assert owner.post(PATH + f"/tasks/{task['id']}/resume").status_code == 409
    cancelled = owner.post(PATH + f"/tasks/{task['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and cancelled["canResume"]
    resumed = owner.post(PATH + f"/tasks/{task['id']}/resume").json()
    assert resumed["id"] == task["id"] and resumed["status"] == "queued"
    assert resumed["clientOperationId"] == task["clientOperationId"]
    with identity_app[1]() as session:
        assert session.get(CanvasLocalJob, int(task["id"])).message_version == 2


@pytest.mark.parametrize(
    "field",
    [
        "source_snapshot",
        "request_json",
        "source_media_id",
        "result_json",
        "lease_token",
        "disposal_receipt_id",
    ],
)
def test_http_actor_cannot_mutate_frozen_inputs_or_executor_receipt(
    identity_app, local_scope, field
):
    owner, user, _, body = local_scope
    task = create(owner, body)
    with identity_app[1]() as session:
        session.info["actor"] = ActorContext(
            int(user["id"]), "owner", user["email"], True, 1, "", "local-guard-test"
        )
        with pytest.raises(WorkflowError):
            with session.begin(), canvas_local_write(session):
                job = session.get(CanvasLocalJob, int(task["id"]))
                value = {} if field.endswith("json") or field == "source_snapshot" else None
                if field == "lease_token":
                    value = "f" * 32
                elif field == "disposal_receipt_id":
                    value = 1
                setattr(job, field, value)
                session.flush()


def test_unauthenticated_request_cannot_insert(identity_app, local_scope):
    anonymous = TestClient(identity_app[0], headers={"Origin": identity_app[2].public_origin})
    try:
        assert (
            anonymous.post(PATH + "/timeline/transcriptions", json=local_scope[-1]).status_code
            == 401
        )
    finally:
        anonymous.close()
