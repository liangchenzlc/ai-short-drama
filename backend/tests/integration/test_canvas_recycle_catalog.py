"""回收站目录与永久处置的账号、代次和重放边界。"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain.canvas import CanvasWriteReceipt
from short_drama.service.canvas_recycle_service import CanvasRecycleService
from tests.integration.test_canvas_project_folders import create_canvas, save_document
from tests.integration.test_canvas_recycle import archive, archive_status, restore
from tests.integration.test_canvas_resources import ROOT, png, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration
CATALOG = "/api/v1/canvas-runtime/recycle-bin"


def purge(client, source_key, archive_key, key="recycle-purge"):
    return client.post(
        f"/api/v1/canvas-runtime/canvas-projects/{source_key}/recycle-purge",
        headers={"Idempotency-Key": key},
        json={"archive_key": archive_key},
    )


def test_catalog_uses_own_current_archive_and_rechecks_membership(identity_app):
    owner, _ = account(identity_app, "catalog_owner")
    member, member_user = account(identity_app, "catalog_member")
    project, path = create_canvas(owner, "catalog-project")
    join(identity_app, owner, member, project["id"], member_user["id"])
    document = member.get(path + "/my-document").json()["source_document"]
    document["nodes"] = [
        {
            "id": "note",
            "type": "text",
            "title": "回收作品",
            "position": {"x": 18, "y": 25},
            "width": 320,
            "height": 220,
            "metadata": {"content": "成员作品", "prompt": "仅成员参数"},
        }
    ]
    assert save_document(member, path, document, "catalog-work").status_code == 200
    before = member.get(path + "/my-document").json()["source_document"]
    archive(member, path, before, "catalog-delete")
    response = member.get(CATALOG)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["archive_key"] == "catalog-delete"
    assert items[0]["source_document"] == before
    assert owner.get(CATALOG).json()["items"] == []
    assert member.get(path).status_code == 404
    assert restore(member, before["id"], "catalog-delete").status_code == 200
    assert member.get(CATALOG).json()["items"] == []
    sibling = owner.post(
        f"/api/v1/projects/{project['id']}/canvases",
        headers={"Idempotency-Key": "catalog-sibling"},
        json={"title": "保留活动画布", "source_key": "catalog-sibling"},
    )
    assert sibling.status_code == 201, sibling.text
    current = member.get(path + "/my-document").json()["source_document"]
    archive(member, path, current, "catalog-delete-next")
    assert [row["archive_key"] for row in member.get(CATALOG).json()["items"]] == [
        "catalog-delete-next"
    ]
    assert owner.delete(
        f"/api/v1/projects/{project['id']}/members/{member_user['id']}"
    ).status_code in (200, 204)
    assert member.get(CATALOG).json()["items"] == []
    assert archive_status(member, before["id"], "catalog-delete-next").status_code == 404
    assert purge(member, before["id"], "catalog-delete-next").status_code == 404


def test_purge_is_terminal_for_the_exact_archive_and_idempotent(identity_app):
    owner, _ = account(identity_app, "purge_owner")
    member, member_user = account(identity_app, "purge_member")
    project, path = create_canvas(owner, "purge-project")
    join(identity_app, owner, member, project["id"], member_user["id"])
    before = owner.get(path + "/my-document").json()["source_document"]
    archive(owner, path, before, "purge-delete")
    assert purge(member, before["id"], "purge-delete").status_code == 404
    assert purge(owner, "wrong-source", "purge-delete").status_code == 404
    response = purge(owner, before["id"], "purge-delete")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "source_key": before["id"],
        "archive_key": "purge-delete",
        "state": "purged",
    }
    assert purge(owner, before["id"], "purge-delete").json() == response.json()
    assert owner.get(CATALOG).json()["items"] == []
    assert archive_status(owner, before["id"], "purge-delete").json()["state"] == "purged"
    assert restore(owner, before["id"], "purge-delete").status_code == 410
    assert owner.get(path).status_code == 404


def test_late_purge_cannot_dispose_a_restored_or_rearchived_work(identity_app):
    client, _ = account(identity_app, "purge_generation")
    _, path = create_canvas(client, "purge-generation")
    before = client.get(path + "/my-document").json()["source_document"]
    archive(client, path, before, "old-delete")
    assert restore(client, before["id"], "old-delete").status_code == 200
    assert purge(client, before["id"], "old-delete").status_code == 409
    current = client.get(path + "/my-document").json()["source_document"]
    archive(client, path, current, "new-delete")
    assert purge(client, before["id"], "old-delete").status_code == 409
    assert [row["archive_key"] for row in client.get(CATALOG).json()["items"]] == ["new-delete"]


def test_archive_snapshot_cannot_be_read_through_public_receipt_after_purge(identity_app):
    client, _ = account(identity_app, "catalog_receipt")
    _, path = create_canvas(client, "catalog-receipt")
    document = client.get(path + "/my-document").json()["source_document"]
    result = archive(client, path, document, "receipt-delete")
    assert "$archive_document" not in result
    assert client.get("/api/v1/canvas-write-receipts/receipt-delete").json()["result"] == result
    assert purge(client, document["id"], "receipt-delete").status_code == 200
    receipt = client.get("/api/v1/canvas-write-receipts/receipt-delete")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["result"] == result


def test_concurrent_restore_and_purge_commit_only_one_outcome(identity_app):
    client, _ = account(identity_app, "catalog_race")
    _, path = create_canvas(client, "catalog-race")
    document = client.get(path + "/my-document").json()["source_document"]
    archive(client, path, document, "race-delete")
    barrier = Barrier(2)

    def request(operation):
        barrier.wait(timeout=10)
        return operation(client, document["id"], "race-delete")

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request, [restore, purge]))
    statuses = [response.status_code for response in responses]
    assert statuses in ([200, 409], [410, 200]), [r.text for r in responses]
    assert client.get(CATALOG).json()["items"] == []
    assert client.get(path).status_code == (200 if statuses[0] == 200 else 404)


def test_failed_purge_receipt_rolls_back_and_releases_archived_scope(identity_app, monkeypatch):
    client, _ = account(identity_app, "purge_rollback")
    _, path = create_canvas(client, "purge-rollback")
    document = client.get(path + "/my-document").json()["source_document"]
    archive(client, path, document, "rollback-delete")
    original = CanvasRecycleService.record_write
    sessions = []

    def fail_after_record(service, **kwargs):
        original(service, **kwargs)
        sessions.append(service.session)
        raise WorkflowError("purge_test_failure", "处置回执写入失败", 409)

    monkeypatch.setattr(CanvasRecycleService, "record_write", fail_after_record)
    response = purge(client, document["id"], "rollback-delete")
    assert response.status_code == 409, response.text
    assert sessions and all("canvas_recycle_scope" not in session.info for session in sessions)
    assert archive_status(client, document["id"], "rollback-delete").json()["state"] == "archived"
    assert len(client.get(CATALOG).json()["items"]) == 1
    assert client.get("/api/v1/canvas-write-receipts/recycle-purge").status_code == 404
    monkeypatch.undo()
    assert restore(client, document["id"], "rollback-delete").status_code == 200


@pytest.mark.parametrize("operation", ["rewrite", "delete"])
def test_archive_receipt_cannot_be_rewritten_or_removed_by_actor(identity_app, operation):
    client, user = account(identity_app, "receipt_immutable")
    _, path = create_canvas(client, "receipt-immutable")
    document = client.get(path + "/my-document").json()["source_document"]
    archive(client, path, document, "immutable-delete")
    assert restore(client, document["id"], "immutable-delete").status_code == 200
    with identity_app[1]() as session:
        session.info["actor"] = ActorContext(
            int(user["id"]), "test", "test@example.test", True, 1, "csrf", "scope"
        )
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.idempotency_key == "immutable-delete"
            )
        )
        assert receipt is not None
        if operation == "rewrite":
            receipt.result_json = {"changed": True}
        else:
            session.delete(receipt)
        with pytest.raises(WorkflowError) as caught:
            session.flush()
        assert caught.value.code == "canvas_receipt_immutable"
        session.rollback()


def test_pre_upgrade_receipt_uses_only_deleting_members_private_document(identity_app):
    owner, _ = account(identity_app, "legacy_catalog_owner")
    member, member_user = account(identity_app, "legacy_catalog_member")
    project, path = create_canvas(owner, "legacy-catalog")
    join(identity_app, owner, member, project["id"], member_user["id"])
    document = owner.get(path + "/my-document").json()["source_document"]
    document["nodes"] = [
        {
            "id": "note",
            "type": "text",
            "title": "作品",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"content": "共享正文", "prompt": "所有者私人参数"},
        }
    ]
    assert save_document(owner, path, document, "legacy-work").status_code == 200
    before = member.get(path + "/my-document").json()["source_document"]
    archive(member, path, before, "legacy-delete")
    # Emulate the stored format from before the frozen-document upgrade.
    with identity_app[1].begin() as session:
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.actor_user_id == int(member_user["id"]),
                CanvasWriteReceipt.idempotency_key == "legacy-delete",
            )
        )
        receipt.result_json = {
            key: value for key, value in receipt.result_json.items() if key != "$archive_document"
        }
    response = member.get(CATALOG)
    assert response.status_code == 200, response.text
    restored = response.json()["items"][0]["source_document"]
    assert restored["revision"] == before["revision"]
    assert restored["nodes"][0]["metadata"] == {"content": "共享正文"}
    assert owner.get(CATALOG).json()["items"] == []


@pytest.mark.skipif(
    os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1", reason="Enable isolated MinIO"
)
def test_recycle_preview_bytes_ranges_and_exact_receipt_authorization(resource_app):
    owner, _ = account(resource_app, "recycle_media_owner")
    member, member_user = account(resource_app, "recycle_media_member")
    project, path = create_canvas(owner, "recycle-media")
    join(resource_app, owner, member, project["id"], member_user["id"])
    document = owner.get(path + "/my-document").json()["source_document"]
    content = png()
    response = upload(owner, content, key="recycle-image", source=document["id"])
    assert response.status_code == 200, response.text
    resource = response.json()["resource"]
    unrelated = upload(owner, png("blue"), key="recycle-unrelated", source=document["id"]).json()[
        "resource"
    ]
    document["nodes"] = [
        {
            "id": "image",
            "type": "image",
            "title": "回收预览",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"storageKey": "resource:" + resource["id"], "content": ""},
        }
    ]
    assert save_document(owner, path, document, "recycle-media-save").status_code == 200
    before = owner.get(path + "/my-document").json()["source_document"]
    archive(owner, path, before, "recycle-media-delete")
    params = {"recycle_source_key": before["id"], "recycle_archive_key": "recycle-media-delete"}
    url = f"{ROOT}/{resource['id']}/file"
    assert owner.get(url).status_code == 404
    preview = owner.get(url, params=params)
    assert preview.status_code == 200, preview.text
    assert preview.content == content
    assert preview.headers["Cache-Control"] == "no-store"
    head = owner.head(url, params=params)
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["Content-Length"]) == len(content)
    partial = owner.get(url, params=params, headers={"Range": "bytes=2-8"})
    assert partial.status_code == 206 and partial.content == content[2:9]
    assert member.get(url, params=params).status_code == 404
    assert owner.get(url, params={**params, "recycle_source_key": "wrong"}).status_code == 404
    assert owner.get(f"{ROOT}/{unrelated['id']}/file", params=params).status_code == 404
    assert owner.get(url, params={"recycle_source_key": before["id"]}).status_code == 422
    assert purge(owner, before["id"], "recycle-media-delete").status_code == 200
    assert owner.get(url, params=params).status_code == 410
