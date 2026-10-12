"""真实 MySQL/登录的深度捕捉准入；本批不运行模型。"""

import json

import pytest
from sqlalchemy import func, select

from short_drama.domain import CanvasLocalJob, MediaFile
from tests.integration.test_canvas_local_admission import local_scope as local_scope
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
PATH = "/api/v1/canvas-runtime"


def payload(scope):
    return {key: value for key, value in scope[-1].items() if key != "language"}


def test_depth_create_replay_and_private_task_center(identity_app, local_scope):
    owner, user, project, _ = local_scope
    body = payload(local_scope)
    response = owner.post(PATH + "/depth-captures", json=body)
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["type"] == "depth_capture" and task["model"] == "video-depth-anything-small"
    assert task["status"] == "queued" and task["provider"] == "local"
    assert json.loads(task["inputJson"])["profile"] == "vda-small-mps-standard-v1"
    replay = owner.post(PATH + "/depth-captures", json=body)
    assert replay.status_code == 200 and replay.json() == task
    member, member_user = account(identity_app, "depth_admission_member")
    join(identity_app, owner, member, project["id"], member_user["id"])
    assert member.get(PATH + f"/tasks/{task['id']}").status_code == 404
    assert member.post(PATH + f"/tasks/{task['id']}/cancel").status_code == 404
    second = member.post(PATH + "/depth-captures", json=body)
    assert second.status_code == 202 and second.json()["id"] != task["id"]
    with identity_app[1]() as session:
        job = session.get(CanvasLocalJob, int(task["id"]))
        assert job.user_id == int(user["id"]) and job.project_id == int(project["id"])
        assert job.source_snapshot["duration_ms"] == 1000
        assert job.source_snapshot["checksum"] == "a" * 64


@pytest.mark.parametrize("duration,accepted", [(15000, True), (15100, True), (15101, False)])
def test_depth_duration_boundary(identity_app, local_scope, duration, accepted):
    body = payload(local_scope)
    with identity_app[1].begin() as session:
        session.get(MediaFile, int(body["resourceId"])).duration_ms = duration
    response = local_scope[0].post(PATH + "/depth-captures", json=body)
    assert response.status_code == (202 if accepted else 400), response.text
    with identity_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasLocalJob)) == int(accepted)


@pytest.mark.parametrize("case", ["audio", "image", "missing", "operation", "profile", "header"])
def test_invalid_depth_request_cannot_insert(identity_app, local_scope, case):
    body = payload(local_scope)
    headers = {}
    expected = 422
    if case in {"audio", "image"}:
        with identity_app[1].begin() as session:
            media = session.get(MediaFile, int(body["resourceId"]))
            media.format_code = "audio/wav" if case == "audio" else "image/png"
            media.width = media.height = None
            if case == "image":
                media.duration_ms = None
        expected = 400
    elif case == "missing":
        body["resourceId"], expected = "1", 404
    elif case == "operation":
        body.pop("clientOperationId")
    elif case == "profile":
        body["profile"] = "untrusted-profile"
    else:
        headers["Idempotency-Key"], expected = "different-operation", 409
    response = local_scope[0].post(PATH + "/depth-captures", json=body, headers=headers)
    assert response.status_code == expected, response.text
    with identity_app[1]() as session:
        assert session.scalar(select(func.count()).select_from(CanvasLocalJob)) == 0


def test_operation_is_shared_between_local_task_types(local_scope):
    owner, _, _, transcription = local_scope
    first = owner.post(PATH + "/timeline/transcriptions", json=transcription)
    assert first.status_code == 202, first.text
    response = owner.post(PATH + "/depth-captures", json=payload(local_scope))
    assert response.status_code == 409, response.text
