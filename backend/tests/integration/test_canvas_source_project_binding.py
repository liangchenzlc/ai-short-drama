"""随机 MySQL 的可信父夹具；不代表真实源库映射或供应商验收。"""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Lock

import pytest
from sqlalchemy import select, update

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db import access
from short_drama.domain import CanvasCreationAttempt, Project, ProjectCanvas
from short_drama.domain.canvas import CanvasRevision, CanvasRevisionUserState, CanvasUserState
from short_drama.service import canvas_service as canvas_service_module
from short_drama.service.base import utcnow
from short_drama.service.canvas_service import CanvasService
from tests.integration.test_canvas_project_folders import create_canvas, save_document
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_SOURCE_BINDING") != "1",
        reason="Explicitly enable isolated source binding verification",
    ),
]


def business(client, name):
    response = client.post("/api/v1/projects", json={"name": name, "aspect": "16:9"})
    assert response.status_code == 201, response.text
    return response.json()


def seed_binding(identity_app, project, business_project):
    # Explicitly trusted fixture seed through a system session, never an HTTP binding API.
    with identity_app[1].begin() as session:
        row = session.get(ProjectCanvas, int(project["primary_canvas_id"]))
        row.source_project_id = int(business_project["id"])


def origin(client, path, key, *, business_id=None):
    source = client.get(path + "/my-document").json()["source_document"]
    document = {"id": key, "workspaceProjectId": key, "title": key, "nodes": []}
    if business_id is not None:
        document["projectId"] = business_id
    return {
        "source_key": key,
        "title": key,
        "source_document": document,
        "source_canvas_key": source["id"],
        "source_canvas_row_version": source["revision"],
    }


def create(client, payload, key):
    return client.post("/api/v1/canvas-workspace", headers={"Idempotency-Key": key}, json=payload)


def actor(user):
    return ActorContext(
        user_id=int(user["id"]),
        username=user["username"],
        email=user["email"],
        verified=True,
        session_id=0,
        csrf_hash="",
        request_id="isolated-source-binding",
    )


def test_free_zip_and_forged_json_keep_binding_null(identity_app):
    owner, _ = account(identity_app, "source_free")
    for key in ("free", "zip"):
        result = create(owner, {"source_key": key, "title": key}, key)
        assert result.status_code == 201, result.text
        assert result.json()["source_project_id"] is None
    fake = create(
        owner,
        {
            "source_key": "fake",
            "source_document": {"id": "fake", "title": "fake", "projectId": "1"},
        },
        "fake",
    )
    assert fake.status_code == 422, fake.text
    result = owner.post(
        f"/api/v1/projects/{result.json()['project_id']}/canvases",
        headers={"Idempotency-Key": "zip-following"},
        json={"source_key": "zip-following", "title": "zip-following"},
    )
    assert result.status_code == 201, result.text
    assert result.json()["source_project_id"] is None


def test_exact_parent_key_inherits_its_own_binding_and_version(identity_app):
    owner, _ = account(identity_app, "source_inherit")
    first, first_path = create_canvas(owner, "first-parent")
    second, second_path = create_canvas(owner, "second-parent")
    first_business = business(owner, "first business")
    second_business = business(owner, "second business")
    seed_binding(identity_app, first, first_business)
    seed_binding(identity_app, second, second_business)
    for index, (path, bound) in enumerate(
        ((first_path, first_business), (second_path, second_business))
    ):
        payload = origin(owner, path, "child-" + str(index), business_id=bound["id"])
        ready = create(owner, payload, "inherit-" + str(index))
        assert ready.status_code == 201, ready.text
        result = ready.json()
        assert result["source_project_id"] == bound["id"]
        assert create(owner, payload, "inherit-" + str(index)).json() == result
        with identity_app[1]() as session:
            attempt = session.scalar(
                select(CanvasCreationAttempt).where(
                    CanvasCreationAttempt.source_key == payload["source_key"]
                )
            )
            assert (
                attempt.request_json["$source_project_binding"]["source_project_id"] == bound["id"]
            )
    payload = origin(owner, first_path, "numeric-impostor")
    payload["source_canvas_key"] = first["primary_canvas_id"]
    assert create(owner, payload, "numeric-impostor").status_code == 404
    payload = origin(owner, first_path, "stale-parent")
    document = owner.get(first_path + "/my-document").json()["source_document"]
    document["title"] = "parent changed"
    assert save_document(owner, first_path, document, "change-parent").status_code == 200
    assert create(owner, payload, "stale-parent").status_code == 409


def test_pending_checks_source_version_before_publishing(identity_app, monkeypatch):
    owner, _ = account(identity_app, "source_pending")
    parent, path = create_canvas(owner, "pending-parent")
    bound = business(owner, "pending business")
    seed_binding(identity_app, parent, bound)
    payload = origin(owner, path, "pending-child")
    original = CanvasService._replace_graph

    def reject(*args, **kwargs):
        raise WorkflowError("injected_final_failure", "Injected final failure", 503)

    monkeypatch.setattr(CanvasService, "_replace_graph", reject)
    assert create(owner, payload, "pending-child").status_code == 503
    with identity_app[1]() as session:
        attempt = session.scalar(
            select(CanvasCreationAttempt).where(CanvasCreationAttempt.source_key == "pending-child")
        )
        frozen = deepcopy(attempt.request_json)
        assert attempt.status == "pending"
    monkeypatch.setattr(CanvasService, "_replace_graph", original)
    document = owner.get(path + "/my-document").json()["source_document"]
    document["title"] = "pending parent advanced"
    assert save_document(owner, path, document, "advance-pending-parent").status_code == 200
    assert create(owner, payload, "pending-child").status_code == 409
    with identity_app[1]() as session:
        attempt = session.scalar(
            select(CanvasCreationAttempt).where(CanvasCreationAttempt.source_key == "pending-child")
        )
        assert attempt.request_json == frozen and attempt.status == "pending"
        assert (
            session.scalar(select(ProjectCanvas).where(ProjectCanvas.source_key == "pending-child"))
            is None
        )


@pytest.mark.parametrize("revoke", ["parent", "business", "target"])
def test_creation_requires_every_current_scope(identity_app, revoke):
    owner, _ = account(identity_app, "source_scope_owner_" + revoke)
    member, user = account(identity_app, "source_scope_member_" + revoke)
    parent, parent_path = create_canvas(owner, "scope-parent-" + revoke)
    target, _ = create_canvas(owner, "scope-target-" + revoke)
    bound = business(owner, "scope business")
    seed_binding(identity_app, parent, bound)
    for project in (parent, target, bound):
        join(identity_app, owner, member, project["id"], user["id"])
    payload = origin(member, parent_path, "scope-child-" + revoke)
    payload["source_document"]["workspaceProjectId"] = target["id"]
    revoked = {"parent": parent, "business": bound, "target": target}[revoke]
    assert owner.delete(f"/api/v1/projects/{revoked['id']}/members/{user['id']}").status_code in (
        200,
        204,
    )
    response = member.post(
        f"/api/v1/projects/{target['id']}/canvases",
        headers={"Idempotency-Key": "scope-create"},
        json=payload,
    )
    assert response.status_code == 404, response.text


def test_ready_replay_checks_business_but_not_old_parent(identity_app):
    owner, _ = account(identity_app, "source_replay_owner")
    member, user = account(identity_app, "source_replay_member")
    parent, path = create_canvas(owner, "replay-parent")
    bound = business(owner, "replay business")
    seed_binding(identity_app, parent, bound)
    for project in (parent, bound):
        join(identity_app, owner, member, project["id"], user["id"])
    payload = origin(member, path, "replay-child")
    ready = create(member, payload, "replay-child")
    assert ready.status_code == 201, ready.text
    assert owner.delete(f"/api/v1/projects/{parent['id']}/members/{user['id']}").status_code in (
        200,
        204,
    )
    assert create(member, payload, "replay-child").json() == ready.json()
    assert owner.delete(f"/api/v1/projects/{bound['id']}/members/{user['id']}").status_code in (
        200,
        204,
    )
    assert create(member, payload, "replay-child").status_code == 404


def test_commit_private_history_and_recycle_never_rebind(identity_app):
    owner, _ = account(identity_app, "source_projections")
    parent, path = create_canvas(owner, "projection-parent")
    bound = business(owner, "projection business")
    seed_binding(identity_app, parent, bound)
    with identity_app[1].begin() as session:
        canvas = session.get(ProjectCanvas, int(parent["primary_canvas_id"]))
        canvas.properties_json = {"projectId": "999", "title": "legacy"}
    document = owner.get(path + "/my-document").json()["source_document"]
    assert document["projectId"] == bound["id"]
    document["projectId"] = "999"
    assert save_document(owner, path, document, "forged-commit").status_code == 422
    document.pop("projectId")
    document["title"] = "new shared title"
    accepted = save_document(owner, path, document, "omitted-binding")
    assert accepted.status_code == 200, accepted.text
    with identity_app[1].begin() as session:
        canvas = session.get(ProjectCanvas, int(parent["primary_canvas_id"]))
        assert canvas.source_project_id == int(bound["id"])
        private = session.scalar(
            select(CanvasUserState).where(CanvasUserState.canvas_id == canvas.id)
        )
        if private is not None:
            private.preferences_json = {**private.preferences_json, "projectId": "999"}
        revision = session.scalar(
            select(CanvasRevision).where(CanvasRevision.canvas_id == canvas.id)
        )
        assert revision is not None
        revision.snapshot_json = {**revision.snapshot_json, "projectId": "999"}
        revision_id = str(revision.id)
        historical_private = session.scalar(
            select(CanvasRevisionUserState).where(
                CanvasRevisionUserState.revision_id == revision.id
            )
        )
        if historical_private is not None:
            state = deepcopy(historical_private.state_json)
            state.setdefault("root", {})["projectId"] = "999"
            historical_private.state_json = state
    assert owner.get(path + "/my-document").json()["source_document"]["projectId"] == bound["id"]
    assert (
        owner.get(path + "/revisions/" + revision_id).json()["source_document"]["projectId"]
        == bound["id"]
    )
    restore_payload = {"expected_row_version": accepted.json()["row_version"]}
    restored = owner.post(
        path + "/revisions/" + revision_id + "/restore",
        headers={"Idempotency-Key": "history-binding"},
        json=restore_payload,
    )
    assert restored.status_code == 200, restored.text
    assert (
        owner.post(
            path + "/revisions/" + revision_id + "/restore",
            headers={"Idempotency-Key": "history-binding"},
            json=restore_payload,
        ).json()
        == restored.json()
    )
    archived = owner.request(
        "DELETE",
        path,
        headers={"Idempotency-Key": "archive-binding"},
        json={"expected_row_version": restored.json()["row_version"]},
    )
    assert archived.status_code == 200, archived.text
    response = owner.post(
        "/api/v1/canvas-runtime/canvas-projects/" + document["id"] + "/recycle-restore",
        headers={"Idempotency-Key": "recycle-binding"},
        json={"archive_key": "archive-binding"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["source_project_id"] == bound["id"]
    assert owner.get(path + "/my-document").json()["source_document"]["projectId"] == bound["id"]


def test_actor_entity_and_bulk_cannot_change_existing_binding(identity_app):
    owner, user = account(identity_app, "source_orm")
    project, _ = create_canvas(owner, "orm-parent")
    bound = business(owner, "orm business")
    seed_binding(identity_app, project, bound)
    with identity_app[1]() as session:
        session.info["actor"] = actor(user)
        row = session.get(ProjectCanvas, int(project["primary_canvas_id"]))
        row.source_project_id = None
        with pytest.raises(WorkflowError, match="可信业务绑定"):
            session.flush()
        session.rollback()
        with pytest.raises(WorkflowError):
            session.execute(
                update(ProjectCanvas)
                .where(ProjectCanvas.id == int(project["primary_canvas_id"]))
                .values(source_project_id=None)
            )
        session.rollback()


@pytest.mark.parametrize("source_kind", ["outside", "archived", "canvas"])
def test_actor_new_binding_requires_accessible_active_standard_project(identity_app, source_kind):
    owner, user = account(identity_app, "source_new_" + source_kind)
    outsider, _ = account(identity_app, "source_other_" + source_kind)
    owning, _ = create_canvas(owner, "source-new-" + source_kind)
    if source_kind == "canvas":
        source_id = owning["id"]
    else:
        source = business(outsider if source_kind == "outside" else owner, "new business")
        source_id = source["id"]
        if source_kind == "archived":
            with identity_app[1].begin() as session:
                session.get(Project, int(source_id)).archived_at = utcnow()
    with identity_app[1]() as session:
        session.info["actor"] = actor(user)
        service = CanvasService(session)
        row = ProjectCanvas(
            **service.audit(),
            project_id=int(owning["id"]),
            source_project_id=int(source_id),
            source_key="new-direct-" + source_kind,
            title="new direct",
            position=1,
            schema_version=1,
            row_version=1,
            properties_json={},
            archived_at=None,
        )
        session.add(row)
        with pytest.raises(NotFound):
            session.flush()
        session.rollback()


def test_pending_recovery_rechecks_business_permission(identity_app, monkeypatch):
    owner, _ = account(identity_app, "source_pending_owner")
    member, user = account(identity_app, "source_pending_member")
    parent, path = create_canvas(owner, "permission-parent")
    bound = business(owner, "permission business")
    seed_binding(identity_app, parent, bound)
    for project in (parent, bound):
        join(identity_app, owner, member, project["id"], user["id"])
    payload = origin(member, path, "permission-child")
    original = CanvasService._replace_graph

    def reject(*args, **kwargs):
        raise WorkflowError("injected_final_failure", "Injected final failure", 503)

    monkeypatch.setattr(CanvasService, "_replace_graph", reject)
    assert create(member, payload, "permission-child").status_code == 503
    monkeypatch.setattr(CanvasService, "_replace_graph", original)
    assert owner.delete(f"/api/v1/projects/{bound['id']}/members/{user['id']}").status_code in (
        200,
        204,
    )
    assert create(member, payload, "permission-child").status_code == 404


def test_opposite_actor_http_creation_keeps_sorted_project_locks(identity_app, monkeypatch):
    first, first_user = account(identity_app, "source_opposite_first")
    second, second_user = account(identity_app, "source_opposite_second")
    first_project, first_path = create_canvas(first, "opposite-first")
    second_project, second_path = create_canvas(second, "opposite-second")
    first_business = business(first, "opposite first business")
    second_business = business(second, "opposite second business")
    seed_binding(identity_app, first_project, first_business)
    seed_binding(identity_app, second_project, second_business)
    join(identity_app, first, second, first_project["id"], second_user["id"])
    join(identity_app, second, first, second_project["id"], first_user["id"])
    first_payload = origin(first, first_path, "opposite-first-child")
    second_payload = origin(second, second_path, "opposite-second-child")
    first_payload["source_document"]["workspaceProjectId"] = second_project["id"]
    second_payload["source_document"]["workspaceProjectId"] = first_project["id"]
    original = access.require_project
    barrier = Barrier(2)
    guard = Lock()
    synchronized = set()
    locks = {int(first_user["id"]): [], int(second_user["id"]): []}

    def trace_authorized_lock(session, identifier, *, owner=False, lock=True):
        user_id = session.info["actor"].user_id
        if lock:
            with guard:
                locks[user_id].append(identifier)
        elif session.info.get("request_project") == identifier:
            with guard:
                synchronize = user_id not in synchronized
                synchronized.add(user_id)
            if synchronize:
                barrier.wait(timeout=15)
        return original(session, identifier, owner=owner, lock=lock)

    monkeypatch.setattr(access, "require_project", trace_authorized_lock)
    monkeypatch.setattr(canvas_service_module, "require_project_access", trace_authorized_lock)

    def submit(client, target, payload, key):
        return client.post(
            f"/api/v1/projects/{target['id']}/canvases",
            headers={"Idempotency-Key": key},
            json=payload,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            submit, first, second_project, first_payload, "opposite-create-first"
        )
        second_future = pool.submit(
            submit, second, first_project, second_payload, "opposite-create-second"
        )
        results = [first_future.result(timeout=30), second_future.result(timeout=30)]
    assert all(response.status_code == 201 for response in results), [
        response.text for response in results
    ]
    for user, bound in ((first_user, first_business), (second_user, second_business)):
        expected = sorted(int(project["id"]) for project in (first_project, second_project, bound))
        assert locks[int(user["id"])][:3] == expected
