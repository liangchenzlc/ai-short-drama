"""回收站恢复使用真实账号和 MySQL，归档回执限定恢复的作品与版本。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.canvas_dao import CanvasDAO
from short_drama.domain import Project, ProjectCanvas
from short_drama.domain.canvas import CanvasNode
from short_drama.service.canvas_recycle_service import CanvasRecycleService
from tests.integration.test_canvas_project_folders import create_canvas, save_document
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration


def archive(client, path, document, key):
    response = client.request(
        "DELETE",
        path,
        headers={"Idempotency-Key": key},
        json={"expected_row_version": document["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def restore(client, source_key, archive_key, key="recycle-restore"):
    return client.post(
        f"/api/v1/canvas-runtime/canvas-projects/{source_key}/recycle-restore",
        headers={"Idempotency-Key": key},
        json={"archive_key": archive_key},
    )


def archive_status(client, source_key, archive_key):
    return client.get(
        f"/api/v1/canvas-runtime/canvas-projects/{source_key}/recycle-status",
        params={"archive_key": archive_key},
    )


def test_unknown_archive_status_is_private_read_only_and_detects_later_restore(identity_app):
    owner, _ = account(identity_app, "archive_status_owner")
    member, member_user = account(identity_app, "archive_status_member")
    project, path = create_canvas(owner, "archive-status")
    join(identity_app, owner, member, project["id"], member_user["id"])
    before = member.get(path + "/my-document").json()["source_document"]
    assert archive_status(member, before["id"], "status-delete").status_code == 404
    archive(member, path, before, "status-delete")
    response = archive_status(member, before["id"], "status-delete")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "source_key": before["id"],
        "project_id": project["id"],
        "archive_key": "status-delete",
        "expected_row_version": before["revision"],
        "committed_row_version": str(int(before["revision"]) + 1),
        "state": "archived",
    }
    assert member.get(path).status_code == 404
    assert archive_status(owner, before["id"], "status-delete").status_code == 404
    assert archive_status(member, "wrong-source", "status-delete").status_code == 404
    assert restore(member, before["id"], "status-delete").status_code == 200
    assert archive_status(member, before["id"], "status-delete").json()["state"] == "superseded"
    current = member.get(path + "/my-document").json()["source_document"]
    archive(member, path, current, "status-delete-again")
    assert archive_status(member, before["id"], "status-delete").json()["state"] == "superseded"
    assert archive_status(member, before["id"], "status-delete-again").json()["state"] == "archived"
    assert (
        restore(member, before["id"], "status-delete-again", "status-restore-again").status_code
        == 200
    )
    assert owner.delete(
        f"/api/v1/projects/{project['id']}/members/{member_user['id']}"
    ).status_code in (200, 204)
    assert archive_status(member, before["id"], "status-delete").status_code == 404


def test_restore_last_canvas_retains_identity_work_and_personal_state(identity_app):
    client, _ = account(identity_app, "recycle_owner")
    project, path = create_canvas(client, "recycle-project")
    document = client.get(path + "/my-document").json()["source_document"]
    document["title"] = "恢复的原作品"
    document["nodes"] = [
        {
            "id": "note",
            "type": "text",
            "title": "正文",
            "position": {"x": 12.125, "y": -18.5},
            "width": 320,
            "height": 220,
            "metadata": {"content": "原有作品", "prompt": "本人参数"},
        }
    ]
    assert save_document(client, path, document, "recycle-save").status_code == 200
    before = client.get(path + "/my-document").json()["source_document"]
    versions = client.get(path + "/revisions").json()["items"]
    assert archive(client, path, before, "recycle-archive")["project_archived"]
    assert client.get(path).status_code == 404
    assert client.get(f"/api/v1/projects/{project['id']}").status_code == 404
    restored = restore(client, before["id"], "recycle-archive")
    assert restored.status_code == 200, restored.text
    assert restored.json()["id"] == project["primary_canvas_id"]
    assert restored.json()["row_version"] == str(int(before["revision"]) + 2)
    after = client.get(path + "/my-document").json()["source_document"]
    for field in ("id", "workspaceProjectId", "title", "nodes", "viewport", "createdAt"):
        assert after[field] == before[field]
    assert client.get(path).json()["source_document"]["nodes"][0]["metadata"] == {
        "content": "原有作品"
    }
    assert client.get(path + "/revisions").json()["items"] == versions
    assert (
        client.get(f"/api/v1/projects/{project['id']}").json()["primary_canvas_id"]
        == (project["primary_canvas_id"])
    )
    replay = restore(client, before["id"], "recycle-archive")
    assert replay.json() == restored.json()
    assert client.get(path).json()["row_version"] == restored.json()["row_version"]
    wrong_key = restore(client, before["id"], "recycle-save", "wrong-archive")
    assert wrong_key.status_code == 404


def test_restore_one_of_several_canvases_keeps_current_primary(identity_app):
    client, _ = account(identity_app, "recycle_multiple")
    project, path = create_canvas(client)
    second = client.post(
        f"/api/v1/projects/{project['id']}/canvases",
        headers={"Idempotency-Key": "recycle-secondary"},
        json={"title": "继续创作", "source_key": "recycle-secondary"},
    ).json()
    before = client.get(path + "/my-document").json()["source_document"]
    assert not archive(client, path, before, "recycle-first")["project_archived"]
    response = restore(client, before["id"], "recycle-first")
    assert response.status_code == 200, response.text
    result = client.get(f"/api/v1/projects/{project['id']}").json()
    assert result["primary_canvas_id"] == second["id"]
    assert result["canvas_count"] == 2


def test_only_current_member_with_own_archive_receipt_can_restore(identity_app):
    owner, _ = account(identity_app, "recycle_private")
    member, member_user = account(identity_app, "recycle_member")
    outsider, _ = account(identity_app, "recycle_outside")
    project, path = create_canvas(owner)
    join(identity_app, owner, member, project["id"], member_user["id"])
    before = member.get(path + "/my-document").json()["source_document"]
    archive(member, path, before, "member-archive")
    assert restore(owner, before["id"], "member-archive").status_code == 404
    assert restore(outsider, before["id"], "member-archive").status_code == 404
    success = restore(member, before["id"], "member-archive")
    assert success.status_code == 200, success.text
    before = member.get(path + "/my-document").json()["source_document"]
    # Retain an active sibling so the owner can revoke through normal collaboration APIs.
    sibling = owner.post(
        f"/api/v1/projects/{project['id']}/canvases",
        headers={"Idempotency-Key": "recycle-member-sibling"},
        json={"title": "保留画布", "source_key": "recycle-member-sibling"},
    )
    assert sibling.status_code == 201, sibling.text
    archive(member, path, before, "member-second-archive")
    removed = owner.delete(f"/api/v1/projects/{project['id']}/members/{member_user['id']}")
    assert removed.status_code in (200, 204), removed.text
    assert restore(member, before["id"], "member-second-archive", "after-revoke").status_code == 404
    assert restore(member, before["id"], "member-archive").status_code == 404
    assert owner.get(path).status_code == 404


def test_concurrent_restore_replays_once_and_old_delete_cannot_restore_new_archive(identity_app):
    client, _ = account(identity_app, "recycle_race")
    _, path = create_canvas(client)
    before = client.get(path + "/my-document").json()["source_document"]
    archive(client, path, before, "race-archive")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: restore(client, before["id"], "race-archive"), range(2)))
    assert [item.status_code for item in results] == [200, 200], [r.text for r in results]
    assert results[0].json() == results[1].json()
    current = client.get(path + "/my-document").json()["source_document"]
    assert current["revision"] == "3"
    archive(client, path, current, "race-next-archive")
    stale = restore(client, before["id"], "race-archive", "old-generation-new-key")
    assert stale.status_code == 409, stale.text
    assert client.get(path).status_code == 404
    # An acknowledgement replay is immutable and cannot repeat its past side effect.
    assert restore(client, before["id"], "race-archive").json() == results[0].json()
    assert client.get(path).status_code == 404
    fresh = restore(client, before["id"], "race-next-archive", "new-generation")
    assert fresh.status_code == 200, fresh.text
    assert client.get(path).json()["row_version"] == "5"


def test_restore_scope_is_narrow_and_failure_rolls_back_parent_canvas_and_receipt(
    identity_app, monkeypatch
):
    client, _ = account(identity_app, "recycle_rollback")
    project, path = create_canvas(client, "restore-target")
    other_project, other_path = create_canvas(client, "other-archived")
    before = client.get(path + "/my-document").json()["source_document"]
    before["nodes"] = [
        {
            "id": "protected",
            "type": "text",
            "title": "作品",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"content": "恢复提交前仍不可读取"},
        }
    ]
    assert save_document(client, path, before, "rollback-work").status_code == 200
    before = client.get(path + "/my-document").json()["source_document"]
    other = client.get(other_path + "/my-document").json()["source_document"]
    archive(client, path, before, "restore-rollback-archive")
    archive(client, other_path, other, "other-archive")
    original_target = CanvasDAO.recycle_target
    observed = []

    def narrow_scope(dao, project_id, canvas_id):
        assert (
            dao.session.scalar(select(Project).where(Project.id == int(other_project["id"])))
            is None
        )
        assert (
            dao.session.scalar(
                select(ProjectCanvas).where(
                    ProjectCanvas.id == int(other_project["primary_canvas_id"])
                )
            )
            is None
        )
        assert (
            dao.session.scalar(select(CanvasNode).where(CanvasNode.canvas_id == canvas_id)) is None
        )
        observed.append(dao.session)
        return original_target(dao, project_id, canvas_id)

    def fail_receipt(_service, **_kwargs):
        raise WorkflowError("restore_receipt_test_failure", "恢复回执写入失败", 409)

    monkeypatch.setattr(CanvasDAO, "recycle_target", narrow_scope)
    monkeypatch.setattr(CanvasRecycleService, "record_write", fail_receipt)
    failed = restore(client, before["id"], "restore-rollback-archive")
    assert failed.status_code == 409, failed.text
    assert failed.json()["error"]["code"] == "restore_receipt_test_failure"
    assert observed and all("canvas_recycle_scope" not in session.info for session in observed)
    assert client.get(path).status_code == 404
    assert client.get(f"/api/v1/projects/{project['id']}").status_code == 404
    assert client.get("/api/v1/canvas-write-receipts/recycle-restore").status_code == 404
    assert client.get(other_path).status_code == 404
    monkeypatch.undo()
    success = restore(client, before["id"], "restore-rollback-archive")
    assert success.status_code == 200, success.text
    assert success.json()["row_version"] == str(int(before["revision"]) + 2)
    assert client.get(other_path).status_code == 404
