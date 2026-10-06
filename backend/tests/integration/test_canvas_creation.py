"""首次整图创建必须同时保存规范媒体与原素材身份。"""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import NotFound, StorageUnavailable, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import CanvasCreationAttempt, CanvasCreationResource, MediaFile, Project
from short_drama.service.base import utcnow
from short_drama.service.canvas_creation_cleanup import cleanup_canvas_creations
from short_drama.service.canvas_service import CanvasService
from tests.integration.test_canvas_resource_copy import image_node, register
from tests.integration.test_canvas_resources import ROOT, account, canvas, png, resource_app, upload
from tests.integration.test_identity_collaboration import identity_app, join

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real isolated canvas MinIO verification",
    ),
]
__all__ = ["resource_app", "identity_app"]


def creation_request(resource, key="atomic-clone"):
    node = image_node(resource)
    node["metadata"]["assetId"] = "creation-original"
    return {
        "source_key": key,
        "title": "完整画布副本",
        "source_document": {
            "id": key,
            "workspaceProjectId": key,
            "revision": "0",
            "title": "完整画布副本",
            "nodes": [node],
            "connections": [],
        },
    }


def test_first_creation_copies_media_and_replays_the_original_request(resource_app):
    owner, _ = account(resource_app, "creation_owner")
    original = upload(owner, png()).json()["resource"]
    register(owner, "creation-original", original)
    payload = creation_request(original)
    unchanged = deepcopy(payload)
    response = owner.post(
        "/api/v1/canvas-workspace", headers={"Idempotency-Key": "atomic-create"}, json=payload
    )
    assert response.status_code == 201, response.text
    result = response.json()
    copied = result["resource_map"][original["id"]]
    assert copied != original["id"] and payload == unchanged
    assert original["id"] in result["resource_aliases"][copied]
    path = f"/api/v1/projects/{result['project_id']}/canvases/{result['id']}"
    document = owner.get(path + "/my-document").json()["source_document"]
    assert len(document["nodes"]) == 1
    assert document["nodes"][0]["metadata"]["storageKey"] == f"resource:{copied}"
    assert document["nodes"][0]["metadata"]["assetId"] == "creation-original"
    assert owner.get(f"{ROOT}/{copied}/file").content == png()
    assert (
        owner.post(
            "/api/v1/canvas-workspace", headers={"Idempotency-Key": "atomic-create"}, json=payload
        ).json()
        == result
    )
    normalized = owner.post(
        ROOT + "/normalize",
        json={
            "canvas_key": result["source_key"],
            "resource_ids": [original["id"]],
        },
    )
    assert normalized.status_code == 200, normalized.text
    assert normalized.json()["resource_map"] == result["resource_map"]


def test_partial_creation_failure_keeps_no_visible_project_and_fixed_retry(
    resource_app, monkeypatch
):
    client, _ = account(resource_app, "creation_partial")
    first = upload(client, png(), key="first").json()["resource"]
    second = upload(client, png("blue"), key="second").json()["resource"]
    payload = creation_request(first)
    node = image_node(second)
    node["id"] = "second"
    payload["source_document"]["nodes"].append(node)
    storage = resource_app[0].state.storage
    copy = storage.copy
    fail = True
    calls = []

    def fail_second(bucket, source, target):
        calls.append(source)
        result = copy(bucket, source, target)
        if fail and source.endswith("/" + second["id"]):
            raise StorageUnavailable("injected lost copy response")
        return result

    monkeypatch.setattr(storage, "copy", fail_second)
    headers = {"Idempotency-Key": "partial-create"}
    failed = client.post("/api/v1/canvas-workspace", headers=headers, json=payload)
    assert failed.status_code == 503, failed.text
    with resource_app[1]() as session:
        assert list(session.scalars(select(Project))) == []
        staged = list(session.scalars(select(CanvasCreationResource)))
        assert sorted(row.status for row in staged) == ["copied", "pending"]
        targets = {str(row.target_resource_id) for row in staged}
        assert len(list(session.scalars(select(MediaFile)))) == 2
    changed = client.post(
        "/api/v1/canvas-workspace", headers=headers, json={**payload, "title": "更改原请求"}
    )
    assert changed.status_code == 409, changed.text
    fail = False
    ready = client.post("/api/v1/canvas-workspace", headers=headers, json=payload)
    assert ready.status_code == 201, ready.text
    assert set(ready.json()["resource_map"].values()) == targets
    assert sum(source.endswith("/" + first["id"]) for source in calls) == 1
    with resource_app[1]() as session:
        assert len(list(session.scalars(select(Project)))) == 1
        assert all(
            row.status == "attached" for row in session.scalars(select(CanvasCreationResource))
        )


def test_graph_failure_rolls_back_publication_and_expired_files_resume(resource_app, monkeypatch):
    client, _ = account(resource_app, "creation_cleanup")
    original = upload(client, png()).json()["resource"]
    payload, headers = creation_request(original), {"Idempotency-Key": "cleanup-create"}
    actual = CanvasService._replace_graph

    def reject_graph(*args, **kwargs):
        raise WorkflowError("injected_graph_failure", "Injected final graph failure", 503)

    monkeypatch.setattr(CanvasService, "_replace_graph", reject_graph)
    rejected = client.post("/api/v1/canvas-workspace", headers=headers, json=payload)
    assert rejected.status_code == 503, rejected.text
    with resource_app[1].begin() as session:
        assert list(session.scalars(select(Project))) == []
        attempt = session.scalar(select(CanvasCreationAttempt))
        attempt.expires_at = utcnow() - timedelta(seconds=1)
        staged = session.scalar(select(CanvasCreationResource))
        target_id = staged.target_resource_id
        assert staged.status == "copied"
    storage = resource_app[0].state.storage
    dry_run = cleanup_canvas_creations(resource_app[1], storage, resource_app[2])
    assert dry_run["examined"] == 1 and dry_run["remove_requests"] == 0
    # Deletion succeeds but its acknowledgement is lost. Durable state must
    # already require copying again; never publish an absent acknowledged file.
    remove = storage.remove

    def lost_remove(*args):
        remove(*args)
        raise StorageUnavailable("injected lost deletion response")

    monkeypatch.setattr(storage, "remove", lost_remove)
    cleaned = cleanup_canvas_creations(resource_app[1], storage, resource_app[2], apply=True)
    assert cleaned["failed"] == 1
    with resource_app[1]() as session:
        staged = session.scalar(select(CanvasCreationResource))
        assert staged.status == "pending" and staged.copied_at is None
        assert staged.source_media_id is None and staged.released_at is not None
    monkeypatch.setattr(storage, "remove", remove)
    monkeypatch.setattr(CanvasService, "_replace_graph", actual)
    resumed = client.post("/api/v1/canvas-workspace", headers=headers, json=payload)
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["resource_map"][original["id"]] == str(target_id)
    assert client.get(f"{ROOT}/{target_id}/file").content == png()
    assert (
        cleanup_canvas_creations(resource_app[1], storage, resource_app[2], apply=True)["examined"]
        == 0
    )


def test_existing_project_reuses_own_media_and_rechecks_receipt_permission(resource_app):
    owner, _ = account(resource_app, "creation_shared_owner")
    member, user = account(resource_app, "creation_shared_member")
    project, _, doc = canvas(owner, "creation-shared")
    join(resource_app, owner, member, project["id"], user["id"])
    original = upload(member, png(), source=doc["id"]).json()["resource"]
    payload = creation_request(original, "new-in-existing")
    payload["source_document"]["workspaceProjectId"] = project["id"]
    path = f"/api/v1/projects/{project['id']}/canvases"
    headers = {"Idempotency-Key": "create-in-existing"}
    response = member.post(path, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["resource_map"] == {original["id"]: original["id"]}
    with resource_app[1]() as session:
        assert list(session.scalars(select(CanvasCreationResource))) == []
    assert owner.delete(f"/api/v1/projects/{project['id']}/members/{user['id']}").status_code in {
        200,
        204,
    }
    assert member.post(path, headers=headers, json=payload).status_code == 404


def test_new_project_cannot_copy_another_authors_private_file(resource_app):
    owner, _ = account(resource_app, "creation_private_owner")
    other, _ = account(resource_app, "creation_private_other")
    original = upload(owner, png()).json()["resource"]
    response = other.post(
        "/api/v1/canvas-workspace",
        headers={"Idempotency-Key": "foreign"},
        json=creation_request(original),
    )
    assert response.status_code == 404
    with resource_app[1]() as session:
        assert list(session.scalars(select(CanvasCreationAttempt))) == []
        assert list(session.scalars(select(Project))) == []


def test_concurrent_creation_and_cleanup_publish_only_one_complete_canvas(
    resource_app, monkeypatch
):
    client, _ = account(resource_app, "creation_concurrent")
    original = upload(client, png()).json()["resource"]
    payload, headers = creation_request(original), {"Idempotency-Key": "concurrent-create"}
    storage = resource_app[0].state.storage
    copy = storage.copy
    entered, finish = Event(), Event()

    def pause_copy(*args):
        result = copy(*args)
        entered.set()
        assert finish.wait(15)
        return result

    monkeypatch.setattr(storage, "copy", pause_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, "/api/v1/canvas-workspace", headers=headers, json=payload)
        assert entered.wait(10)
        with resource_app[1].begin() as session:
            session.scalar(select(CanvasCreationAttempt)).expires_at = utcnow() - timedelta(
                seconds=1
            )
            assert list(session.scalars(select(Project))) == []
        try:
            cleaned = cleanup_canvas_creations(
                resource_app[1], storage, resource_app[2], apply=True
            )
            assert cleaned["failed"] == 1 and cleaned["remove_requests"] == 0
            second = pool.submit(
                client.post, "/api/v1/canvas-workspace", headers=headers, json=payload
            )
        finally:
            finish.set()
        responses = [first.result(timeout=15), second.result(timeout=15)]
    assert all(response.status_code == 201 for response in responses), [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    copied = responses[0].json()["resource_map"][original["id"]]
    assert client.get(f"{ROOT}/{copied}/file").content == png()


@pytest.mark.parametrize("copy_acknowledged", [False, True])
def test_source_revocation_distinguishes_pending_bytes_and_independent_copy(
    resource_app, monkeypatch, copy_acknowledged
):
    owner, _ = account(resource_app, "creation_revoke_owner")
    member, user = account(resource_app, "creation_revoke_member")
    project, path, doc = canvas(owner, "creation-revoke-source")
    join(resource_app, owner, member, project["id"], user["id"])
    original = upload(owner, png(), source=doc["id"]).json()["resource"]
    doc["nodes"] = [image_node(original)]
    assert (
        owner.post(
            path + "/commits",
            headers={"Idempotency-Key": "publish"},
            json={
                "expected_row_version": doc["revision"],
                "source_document": doc,
            },
        ).status_code
        == 200
    )
    payload, headers = creation_request(original), {"Idempotency-Key": "revoke-create"}
    storage = resource_app[0].state.storage
    copy, graph = storage.copy, CanvasService._replace_graph

    def revoke():
        assert owner.delete(
            f"/api/v1/projects/{project['id']}/members/{user['id']}"
        ).status_code in {200, 204}

    if copy_acknowledged:

        def reject_graph(*args):
            raise WorkflowError("injected_graph_failure", "Injected graph failure", 503)

        monkeypatch.setattr(CanvasService, "_replace_graph", reject_graph)
        assert (
            member.post("/api/v1/canvas-workspace", headers=headers, json=payload).status_code
            == 503
        )
        monkeypatch.setattr(CanvasService, "_replace_graph", graph)
        revoke()
        result = member.post("/api/v1/canvas-workspace", headers=headers, json=payload)
        assert result.status_code == 201, result.text
        copied = result.json()["resource_map"][original["id"]]
        assert member.get(f"{ROOT}/{copied}/file").content == png()
    else:

        def revoke_after_io(*args):
            result = copy(*args)
            revoke()
            return result

        monkeypatch.setattr(storage, "copy", revoke_after_io)
        result = member.post("/api/v1/canvas-workspace", headers=headers, json=payload)
        assert result.status_code == 404, result.text
        with resource_app[1]() as session:
            assert len(list(session.scalars(select(Project)))) == 1
            assert session.scalar(select(CanvasCreationResource)).status == "pending"


def test_preparation_rows_are_private_and_request_identity_cannot_change(resource_app, monkeypatch):
    client, user = account(resource_app, "creation_scope_owner")
    _, other = account(resource_app, "creation_scope_other")
    original = upload(client, png()).json()["resource"]

    def unavailable(*args):
        raise StorageUnavailable("injected pending preparation")

    monkeypatch.setattr(resource_app[0].state.storage, "copy", unavailable)
    assert (
        client.post(
            "/api/v1/canvas-workspace",
            headers={"Idempotency-Key": "scope-create"},
            json=creation_request(original),
        ).status_code
        == 503
    )

    def actor(row):
        return ActorContext(int(row["id"]), "test", "test@example.test", True, 1, "csrf", "scope")

    with resource_app[1]() as session:
        session.info["actor"] = actor(other)
        assert list(session.scalars(select(CanvasCreationAttempt))) == []
        assert list(session.scalars(select(CanvasCreationResource))) == []
    for model, field, value in (
        (CanvasCreationAttempt, "request_hash", "a" * 64),
        (CanvasCreationResource, "target_resource_id", 123),
        (CanvasCreationResource, "user_id", int(other["id"])),
    ):
        with resource_app[1]() as session:
            session.info["actor"] = actor(user)
            with pytest.raises((WorkflowError, NotFound)), session.begin():
                row = session.scalar(select(model))
                setattr(row, field, value)
                session.flush()
