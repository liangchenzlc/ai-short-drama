"""项目删除在隔离 MySQL 中校验所有者、版本和保留历史的边界。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from short_drama.domain import AsyncTask, MediaFile, Project, ProjectCanvas, ProjectNovel
from short_drama.service.task_access import may_submit
from tests.integration.standard.identity.test_identity_collaboration import account, identity_app

pytestmark = pytest.mark.integration
__all__ = ["identity_app"]


def test_project_crud_delete_permissions_versions_and_retained_history(identity_app, db_session):
    owner, user = account(identity_app, "project_delete_owner")
    other, _ = account(identity_app, "project_delete_other")
    root = "/api/v1/projects"
    first = owner.post(root, json={"name": "story%one"})
    second = owner.post(root, json={"name": "other"})
    assert first.status_code == second.status_code == 201
    project_id, other_id = first.json()["id"], second.json()["id"]
    path = f"{root}/{project_id}"
    listing = owner.get(root, params={"q": "%", "limit": 1}).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == project_id
    assert owner.get(root, params={"offset": 1, "limit": 1}).json()["total"] == 2
    assert owner.post(path + "/open").status_code == 200
    assert owner.get(root).json()["items"][0]["id"] == project_id
    assert (
        owner.put(
            path + "/novel", json={"content": "preserved novel", "content_version": "1"}
        ).status_code
        == 200
    )
    renamed = owner.patch(path, json={"name": "renamed", "row_version": "1"})
    assert renamed.status_code == 200 and renamed.json()["row_version"] == "2"
    assert owner.delete(path).status_code == 422
    conflict = owner.delete(path, params={"row_version": "1"})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "project_version_conflict"
    assert other.delete(path, params={"row_version": "2"}).status_code == 404
    assert (
        owner.delete(
            path, headers={"X-CSRF-Token": "wrong"}, params={"row_version": "2"}
        ).status_code
        == 403
    )
    shot = owner.post(
        path + "/shots",
        json={"storyboard_version": "1", "script": "belongs to this project"},
        headers={"Idempotency-Key": "project-delete-shot"},
    )
    assert shot.status_code == 201, shot.text
    shot_id = shot.json()["shot"]["id"]
    assert (
        other.request(
            "DELETE",
            f"{path}/shots/{shot_id}",
            json={"row_version": "1", "storyboard_version": "2"},
        ).status_code
        == 404
    )
    assert (
        owner.request(
            "DELETE",
            f"{root}/{other_id}/shots/{shot_id}",
            json={"row_version": "1", "storyboard_version": "1"},
        ).status_code
        == 404
    )
    with db_session.begin():
        task = AsyncTask(
            id=8001,
            project_id=int(project_id),
            initiated_by=int(user["id"]),
            service_type="image",
            status="running",
            idempotency_key="project-delete-task",
            request_hash="a" * 64,
        )
        media = MediaFile(
            id=8002,
            project_id=int(project_id),
            created_by=int(user["id"]),
            format_code="image/png",
            storage_locator="minio://test/keep.png",
            original_name="keep.png",
        )
        db_session.add_all([task, media])
        source = SimpleNamespace(initiated_by=int(user["id"]), project_id=int(project_id))
        assert may_submit(db_session, source)
    deleted = owner.delete(path, params={"row_version": "2"})
    assert deleted.status_code == 204 and deleted.content == b""
    assert owner.get(path).status_code == owner.post(path + "/open").status_code == 404
    assert owner.get(path + "/writing").status_code == 404
    assert owner.get(path + "/shots").status_code == 404
    assert owner.delete(path, params={"row_version": "2"}).status_code == 404
    assert [item["id"] for item in owner.get(root).json()["items"]] == [other_id]
    with db_session.begin():
        project = db_session.scalar(select(Project).where(Project.id == int(project_id)))
        assert project.archived_at is not None and project.row_version == 3
        assert db_session.scalar(select(ProjectNovel.content)) == "preserved novel"
        assert db_session.get(AsyncTask, 8001).status == "running"
        assert db_session.get(MediaFile, 8002).storage_locator == "minio://test/keep.png"
        assert not may_submit(db_session, source)


def test_canvas_project_delete_hides_workspace_and_invalidates_recycle(identity_app, db_session):
    owner, _ = account(identity_app, "canvas_project_delete")
    root = "/api/v1/projects"
    created = owner.post(
        root,
        json={"name": "canvas", "workspace_mode": "infinite_canvas"},
        headers={"Idempotency-Key": "canvas-project-delete"},
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    canvas_root = f"{root}/{project_id}/canvases"
    first = owner.get(canvas_root).json()["items"][0]
    second = owner.post(
        canvas_root, json={"title": "second"}, headers={"Idempotency-Key": "delete-second"}
    )
    assert second.status_code == 201, second.text
    archive = owner.request(
        "DELETE",
        f"{canvas_root}/{first['id']}",
        json={"expected_row_version": first["row_version"]},
        headers={"Idempotency-Key": "archive-before-project-delete"},
    )
    assert archive.status_code == 200, archive.text
    assert len(owner.get("/api/v1/canvas-runtime/recycle-bin").json()["items"]) == 1
    deleted = owner.delete(f"{root}/{project_id}", params={"row_version": "1"})
    assert deleted.status_code == 204, deleted.text
    assert owner.get(root).json()["total"] == 0
    assert owner.get(canvas_root).status_code == 404
    assert owner.get("/api/v1/canvas-runtime/recycle-bin").json()["items"] == []
    restored = owner.post(
        f"/api/v1/canvas-runtime/canvas-projects/{first['source_key']}/recycle-restore",
        json={"archive_key": "archive-before-project-delete"},
        headers={"Idempotency-Key": "restore-deleted-project"},
    )
    assert restored.status_code == 409, restored.text
    assert owner.get(root).json()["total"] == 0
    with db_session.begin():
        rows = list(db_session.scalars(select(ProjectCanvas)))
        assert len(rows) == 2 and all(row.archived_at is not None for row in rows)
