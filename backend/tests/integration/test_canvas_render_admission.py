"""真实MySQL/登录上的本地渲染准入；本批不执行编码或MinIO上传。"""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from short_drama.core.exceptions import WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db.canvas_render_access import canvas_render_write
from short_drama.domain import AsyncTask, CanvasRenderJob, CanvasRenderMediaReference, MediaFile
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
PATH = "/api/v1/canvas-runtime"


@pytest.fixture
def render_scope(identity_app):
    owner, user = account(identity_app, "render_admission_owner")
    response = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "render-admission-project"},
        json={"name": "真实渲染准入", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    document = owner.get(
        f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}/my-document"
    ).json()["source_document"]
    mid = add_media(identity_app, project["id"], user["id"])
    body = {
        "projectId": document["id"],
        "clientOperationId": "render-admission-operation",
        "timeline": {
            "version": 2,
            "durationMs": 1000,
            "tracks": [{"id": "v"}],
            "clips": [
                {
                    "id": "clip",
                    "kind": "video",
                    "trackId": "v",
                    "nodeId": "editor-node",
                    "durationMs": 1000,
                    "directMedia": {"storageKey": f"resource:{mid}"},
                }
            ],
        },
        "options": {"width": 320, "height": 180},
    }
    return owner, user, project, mid, body


def add_media(identity_app, project_id, user_id, *, published=True):
    identifier = next_id()
    settings = identity_app[2]
    with identity_app[1].begin() as session:
        session.add(
            MediaFile(
                id=identifier,
                project_id=int(project_id),
                scope_user_id=None,
                created_by=int(user_id),
                updated_by=int(user_id),
                format_code="video/mp4",
                storage_locator=f"minio://{settings.minio_video_bucket}/canvas/admission/{identifier}.mp4",
                original_name="admission metadata fixture.mp4",
                width=320,
                height=180,
                duration_ms=1000,
                byte_size=1024,
                checksum_sha256="a" * 64,
                published_at=utcnow() if published else None,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    return str(identifier)


def create(owner, body):
    response = owner.post(PATH + "/timeline/renders", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def count_jobs(identity_app):
    with identity_app[1]() as session:
        return session.scalar(select(func.count()).select_from(CanvasRenderJob))


def test_create_replay_frozen_plan_and_task_center_without_ai(identity_app, render_scope):
    owner, user, project, mid, body = render_scope
    task = create(owner, body)
    assert task["type"] == "timeline_render" and task["status"] == "queued"
    assert task["clientOperationId"] == body["clientOperationId"]
    assert isinstance(task["id"], str) and int(task["id"]) > 2**53
    assert owner.post(PATH + "/timeline/renders", json=body).json() == task
    response = owner.get(PATH + f"/tasks/{task['id']}")
    assert response.status_code == 200 and response.json() == task
    listed = owner.get(PATH + "/tasks", params={"projectId": body["projectId"]}).json()
    assert [item["id"] for item in listed] == [task["id"]]
    logs = owner.get(PATH + f"/tasks/{task['id']}/logs")
    assert logs.status_code == 200 and logs.json()[0]["level"] == "info"
    with identity_app[1]() as session:
        job = session.get(CanvasRenderJob, int(task["id"]))
        assert (job.user_id, job.project_id, job.canvas_id) == (
            int(user["id"]),
            int(project["id"]),
            int(project["primary_canvas_id"]),
        )
        assert job.plan_json["segments"][0]["sourceId"] == mid
        refs = list(session.scalars(select(CanvasRenderMediaReference)))
        assert len(refs) == 1 and refs[0].media_id == int(mid)
        assert job.source_snapshot[mid]["checksum"] == "a" * 64
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 0


def test_operation_conflicts_and_missing_identity_do_not_insert(identity_app, render_scope):
    owner, _, _, _, body = render_scope
    create(owner, body)
    different = deepcopy(body)
    different["timeline"]["clips"][0]["volume"] = 0.5
    changed = owner.post(PATH + "/timeline/renders", json=different)
    assert (
        changed.status_code == 409
        and changed.json()["error"]["code"] == "canvas_render_operation_conflict"
    )
    mismatch = owner.post(
        PATH + "/timeline/renders", json=body, headers={"Idempotency-Key": "different-operation"}
    )
    assert mismatch.status_code == 409
    without = {key: value for key, value in body.items() if key != "clientOperationId"}
    assert owner.post(PATH + "/timeline/renders", json=without).status_code == 422
    assert count_jobs(identity_app) == 1


def test_members_keep_separate_tasks_with_same_client_operation(identity_app, render_scope):
    owner, _, project, _, body = render_scope
    member, member_user = account(identity_app, "render_admission_member")
    outsider, _ = account(identity_app, "render_admission_outsider")
    join(identity_app, owner, member, project["id"], member_user["id"])
    first, second = create(owner, body), create(member, body)
    assert first["id"] != second["id"]
    assert member.get(PATH + f"/tasks/{first['id']}").status_code == 404
    assert owner.get(PATH + f"/tasks/{second['id']}").status_code == 404
    assert outsider.post(PATH + "/timeline/renders", json=body).status_code == 404
    assert [item["id"] for item in member.get(PATH + "/tasks").json()] == [second["id"]]


@pytest.mark.parametrize("source", ["missing", "private", "cross_project", "url"])
def test_unavailable_or_out_of_scope_source_rejects_before_insert(
    identity_app, render_scope, source
):
    owner, user, project, _, body = render_scope
    if source == "missing":
        mid = str(next_id())
    elif source == "url":
        mid = "https://untrusted.example/source.mp4"
    elif source == "private":
        _, other = account(identity_app, "render_source_other")
        mid = add_media(identity_app, project["id"], other["id"], published=False)
    else:
        response = owner.post(
            "/api/v1/projects",
            headers={"Idempotency-Key": "render-source-other-project"},
            json={"name": "另一个画布", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
        )
        assert response.status_code == 201, response.text
        mid = add_media(identity_app, response.json()["id"], user["id"])
    changed = deepcopy(body)
    changed["timeline"]["clips"][0]["directMedia"]["storageKey"] = f"resource:{mid}"
    response = owner.post(PATH + "/timeline/renders", json=changed)
    assert response.status_code == 400, response.text
    assert count_jobs(identity_app) == 0


def test_cancel_and_explicit_resume_preserve_original_snapshot(identity_app, render_scope):
    owner, _, _, _, body = render_scope
    created = create(owner, body)
    cancelled = owner.post(PATH + f"/tasks/{created['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["canResume"]
    resumed = owner.post(PATH + f"/tasks/{created['id']}/resume")
    assert resumed.status_code == 200 and resumed.json()["status"] == "queued"
    assert resumed.json()["id"] == created["id"]
    assert owner.post(PATH + f"/tasks/{created['id']}/resume").status_code == 409
    with identity_app[1]() as session:
        job = session.get(CanvasRenderJob, int(created["id"]))
        assert (
            job.message_version == 2
            and job.request_json["clientOperationId"] == body["clientOperationId"]
        )
        assert job.cancel_requested == 0 and job.finished_at is None


def test_create_checks_real_csrf_actor_epoch_and_authentication(identity_app, render_scope):
    owner, user, _, _, body = render_scope
    other, _ = account(identity_app, "render_epoch_other")
    assert (
        owner.post(
            PATH + "/timeline/renders", json=body, headers={"X-CSRF-Token": "invalid"}
        ).status_code
        == 403
    )
    switched = other.post(
        PATH + "/timeline/renders", json=body, headers={"X-Canvas-Actor": user["id"]}
    )
    assert (
        switched.status_code == 409 and switched.json()["error"]["code"] == "canvas_actor_changed"
    )
    anonymous = TestClient(identity_app[0], headers={"Origin": identity_app[2].public_origin})
    assert anonymous.post(PATH + "/timeline/renders", json=body).status_code == 401
    assert count_jobs(identity_app) == 0


@pytest.mark.parametrize("change", ["authority", "snapshot", "reference", "disposal"])
def test_orm_authority_and_snapshot_are_not_ordinary_project_writes(
    identity_app, render_scope, change
):
    owner, user, _, _, body = render_scope
    created = create(owner, body)
    with identity_app[1]() as session:
        session.info["actor"] = ActorContext(
            int(user["id"]), "owner", "", True, 1, "", "render-test"
        )
        with pytest.raises(WorkflowError) as failure, session.begin():
            job = session.get(CanvasRenderJob, int(created["id"]))
            if change == "authority":
                job.progress = 40
                session.flush()
            else:
                with canvas_render_write(session):
                    if change == "snapshot":
                        job.request_json = {"forged": True}
                    elif change == "disposal":
                        job.disposal_receipt_id = 1
                    else:
                        reference = session.scalar(
                            select(CanvasRenderMediaReference).where(
                                CanvasRenderMediaReference.job_id == job.id
                            )
                        )
                        reference.source_key = "forged"
                    session.flush()
        assert failure.value.status_code == 403
