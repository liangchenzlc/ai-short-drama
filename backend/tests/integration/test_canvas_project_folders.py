"""原版项目文件夹合同、私人分类及保存/删除竞争使用真实 MySQL。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import select

from short_drama.dao.canvas_dao import CanvasDAO
from short_drama.domain.canvas import CanvasRevision
from short_drama.domain.canvas_folder import CanvasProjectFolderItem
from short_drama.service.base import utcnow
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
FOLDERS = "/api/v1/canvas-runtime/canvas-folders"


def put_folder(client, key="folder-one", name="我的项目", **fields):
    return client.put(FOLDERS + "/" + key, json={"folder": {"id": key, "name": name, **fields}})


def create_canvas(client, key="folder-project"):
    response = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": key},
        json={"name": "文件夹中的作品", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    return project, f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"


def save_document(client, path, document, key):
    return client.post(
        path + "/commits",
        headers={"Idempotency-Key": key},
        json={"expected_row_version": document["revision"], "source_document": document},
    )


def test_source_folder_contract_names_timestamps_tombstone_and_account_scope(identity_app):
    owner, _ = account(identity_app, "project_folder_owner")
    other, _ = account(identity_app, "project_folder_other")
    created = put_folder(owner, name="  中文项目 🎬  ", createdAt="2024-01-02T03:04:05Z")
    assert created.status_code == 200, created.text
    folder = created.json()["folder"]
    assert folder["name"] == "中文项目 🎬" and folder["createdAt"] == "2024-01-02T03:04:05Z"
    assert put_folder(owner, "duplicate-name", name=folder["name"]).status_code == 200
    assert put_folder(owner, "empty-name", name="  ").json()["folder"]["name"] == "未命名文件夹"
    assert put_folder(owner, "long-name", name="🎬" * 80).status_code == 200
    assert put_folder(owner, "long-name", name="字" * 81).status_code == 422
    assert put_folder(owner, "x" * 81).status_code == 422
    mismatch = owner.put(FOLDERS + "/wrong", json={"folder": {"id": "different", "name": "错"}})
    assert mismatch.status_code == 422
    assert other.get(FOLDERS).json() == {"folders": []}
    assert other.delete(FOLDERS + "/folder-one").status_code == 404
    assert put_folder(other, name="另一个账号，同一个不透明 ID").status_code == 200
    renamed = put_folder(owner, name="改名", createdAt="2030-01-01T00:00:00Z")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["folder"]["createdAt"] == folder["createdAt"]
    assert owner.delete(FOLDERS + "/folder-one").status_code == 200
    assert owner.delete(FOLDERS + "/folder-one").status_code == 200
    rejected = put_folder(owner)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "canvas_folder_deleted"
    assert all(row["id"] != "folder-one" for row in owner.get(FOLDERS).json()["folders"])
    assert other.get(FOLDERS).json()["folders"][0]["name"] == "另一个账号，同一个不透明 ID"


def test_folder_membership_is_private_delete_preserves_work_and_conflicts_stale_save(
    identity_app, database_errors
):
    owner, _ = account(identity_app, "folder_canvas_owner")
    member, member_user = account(identity_app, "folder_canvas_member")
    project, path = create_canvas(owner)
    join(identity_app, owner, member, project["id"], member_user["id"])
    assert put_folder(owner).status_code == 200
    assert put_folder(member, "member-folder").status_code == 200
    doc = owner.get(path + "/my-document").json()["source_document"]
    doc["folderId"] = "folder-one"
    saved = save_document(owner, path, doc, "move-owner")
    assert saved.status_code == 200, saved.text
    assert int(saved.json()["row_version"]) == int(doc["revision"]) + 1
    assert owner.get(path + "/my-document").json()["source_document"]["folderId"] == "folder-one"
    assert not owner.get(path).json()["source_document"].get("folderId")
    other_doc = member.get(path + "/my-document").json()["source_document"]
    assert not other_doc.get("folderId")
    other_doc["folderId"] = "member-folder"
    assert save_document(member, path, other_doc, "move-member").status_code == 200
    stale = owner.get(path + "/my-document").json()["source_document"]
    assert stale["folderId"] == "folder-one"
    listing = owner.get("/api/v1/canvas-workspace").json()["items"]
    assert listing[0]["folder_id"] == "folder-one"
    assert owner.delete(FOLDERS + "/folder-one").status_code == 200
    current = owner.get(path + "/my-document").json()["source_document"]
    assert not current.get("folderId") and current["title"] == stale["title"]
    assert int(current["revision"]) == int(stale["revision"]) + 1
    assert (
        member.get(path + "/my-document").json()["source_document"]["folderId"] == "member-folder"
    )
    conflict = save_document(owner, path, stale, "old-save")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "canvas_revision_conflict"
    current["folderId"] = "folder-one"
    rejected = save_document(owner, path, current, "deleted-folder")
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "canvas_folder_missing"
    assert database_errors == []


def test_concurrent_move_and_delete_never_leave_or_revive_a_folder(identity_app, database_errors):
    owner, _ = account(identity_app, "folder_move_race")
    _, path = create_canvas(owner)
    assert put_folder(owner).status_code == 200
    doc = owner.get(path + "/my-document").json()["source_document"]
    doc["folderId"] = "folder-one"
    with ThreadPoolExecutor(max_workers=2) as pool:
        move = pool.submit(save_document, owner, path, doc, "racing-move")
        deletion = pool.submit(owner.delete, FOLDERS + "/folder-one")
        moved, deleted = move.result(timeout=15), deletion.result(timeout=15)
    assert moved.status_code in {200, 422}, moved.text
    assert deleted.status_code == 200, deleted.text
    assert not owner.get(path + "/my-document").json()["source_document"].get("folderId")
    assert database_errors == []


def test_history_restores_live_folder_but_rejects_deleted_folder_without_changing_work(
    identity_app,
):
    owner, _ = account(identity_app, "folder_history")
    project, path = create_canvas(owner)
    assert put_folder(owner).status_code == 200
    doc = owner.get(path + "/my-document").json()["source_document"]
    doc["folderId"] = "folder-one"
    assert save_document(owner, path, doc, "history-in").status_code == 200
    with identity_app[1].begin() as session:
        for row in session.scalars(select(CanvasRevision)):
            row.created_at = utcnow() - timedelta(minutes=6)
    doc = owner.get(path + "/my-document").json()["source_document"]
    doc.pop("folderId")
    assert save_document(owner, path, doc, "history-out").status_code == 200
    versions = owner.get(path + "/revisions").json()["items"]
    entry = versions[0]
    historical = owner.get(path + "/revisions/" + entry["id"]).json()["source_document"]
    assert historical["folderId"] == "folder-one"
    restored = owner.post(
        path + "/revisions/" + entry["id"] + "/restore",
        headers={"Idempotency-Key": "history-folder-restore"},
        json={"expected_row_version": str(int(doc["revision"]) + 1)},
    )
    assert restored.status_code == 200, restored.text
    assert owner.get(path + "/my-document").json()["source_document"]["folderId"] == "folder-one"
    assert owner.delete(FOLDERS + "/folder-one").status_code == 200
    before = owner.get(path + "/my-document").json()
    rejected = owner.post(
        path + "/revisions/" + entry["id"] + "/restore",
        headers={"Idempotency-Key": "history-deleted-folder"},
        json={"expected_row_version": before["row_version"]},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "canvas_folder_missing"
    assert owner.get(path + "/my-document").json() == before
    with identity_app[1]() as session:
        assert (
            session.scalar(
                select(CanvasProjectFolderItem).where(
                    CanvasProjectFolderItem.canvas_id == int(project["primary_canvas_id"])
                )
            )
            is None
        )


def test_delete_owned_folder_after_membership_revocation_clears_only_personal_assignment(
    identity_app,
):
    owner, _ = account(identity_app, "folder_revoke_owner")
    member, member_user = account(identity_app, "folder_revoke_member")
    project, path = create_canvas(owner)
    join(identity_app, owner, member, project["id"], member_user["id"])
    assert put_folder(member).status_code == 200
    doc = member.get(path + "/my-document").json()["source_document"]
    doc["folderId"] = "folder-one"
    assert save_document(member, path, doc, "member-folder-before-revoke").status_code == 200
    version = owner.get(path + "/my-document").json()["row_version"]
    assert (
        owner.delete(f"/api/v1/projects/{project['id']}/members/{member_user['id']}").status_code
        == 200
    )
    assert member.get(path + "/my-document").status_code == 404
    assert member.delete(FOLDERS + "/folder-one").status_code == 200
    assert owner.get(path + "/my-document").json()["row_version"] == version
    with identity_app[1]() as session:
        assert session.scalar(select(CanvasProjectFolderItem)) is None


def test_folder_delete_waits_for_member_save_and_uses_its_current_canvas_version(
    identity_app, database_errors, monkeypatch
):
    owner, _ = account(identity_app, "folder_wait_owner")
    member, member_user = account(identity_app, "folder_wait_member")
    project, path = create_canvas(owner)
    join(identity_app, owner, member, project["id"], member_user["id"])
    assert put_folder(owner).status_code == 200
    doc = owner.get(path + "/my-document").json()["source_document"]
    doc["folderId"] = "folder-one"
    assert save_document(owner, path, doc, "folder-wait-move").status_code == 200
    member_doc = member.get(path + "/my-document").json()["source_document"]
    member_doc["title"] = "成员先完成的新标题"
    selected, saved = Event(), Event()
    original = CanvasDAO.project

    def wait_before_project_lock(dao, identifier, *, lock=False):
        if lock and not selected.is_set():
            selected.set()
            assert saved.wait(10), "member save did not complete"
        return original(dao, identifier, lock=lock)

    monkeypatch.setattr(CanvasDAO, "project", wait_before_project_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted = pool.submit(owner.delete, FOLDERS + "/folder-one")
        assert selected.wait(10)
        try:
            committed = save_document(member, path, member_doc, "member-before-folder-delete")
            assert committed.status_code == 200, committed.text
        finally:
            saved.set()
        result = deleted.result(timeout=15)
    assert result.status_code == 200, (result.text, database_errors)
    current = owner.get(path + "/my-document").json()["source_document"]
    assert current["title"] == member_doc["title"]
    assert int(current["revision"]) == int(committed.json()["row_version"]) + 1
    assert not current.get("folderId")
    assert database_errors == []
