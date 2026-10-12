"""嵌套私人子文档的真实 HTTP/MySQL 隔离、稳定身份与历史不可变性。"""

from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from short_drama.domain.canvas import CanvasRevision, CanvasRevisionUserState, CanvasWriteReceipt
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = pytest.mark.integration


def read_document(client: TestClient, path: str, *, private: bool = True) -> dict[str, Any]:
    response = client.get(path + ("/my-document" if private else ""))
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["row_version"], str) and body["row_version"].isdecimal()
    assert isinstance(body["source_document"]["revision"], str)
    return body


def commit_document(
    client: TestClient, path: str, document: dict[str, Any], key: str
) -> dict[str, Any]:
    response = client.post(
        path + "/commits",
        headers={"Idempotency-Key": key},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert isinstance(result["row_version"], str) and result["row_version"].isdecimal()
    return result


def entries(document: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    if kind == "storyboard":
        return document["nodes"][0]["metadata"]["storyboard"]["rows"]
    if kind == "timeline":
        return document["timeline"]["clips"]
    return document["directorScenes"][0]["objects"]


def private_fields(author: str, suffix: str) -> dict[str, Any]:
    return {
        "taskId": "18446744073709551615" if author == "owner" else "18446744073709551614",
        "modelConfig": {"modelConfigId": "9007199254740997", "label": f"{author}-{suffix}"},
        "prompt": f"{author} 私人参数 {suffix}",
        "errorDetails": f"{author} 私人诊断 {suffix}",
    }


def assert_public(items: list[dict[str, Any]]) -> None:
    for item in items:
        assert set(item) == {"id", "name"}


@pytest.mark.parametrize("kind", ["storyboard", "timeline", "director"])
@pytest.mark.parametrize("change", ["reorder", "delete"])
def test_nested_private_state_survives_member_edits_without_entering_history_or_receipts(
    identity_app: Any, database_errors: list, kind: str, change: str
) -> None:
    owner, owner_user = account(identity_app, f"proj_{kind}_{change}_o")
    member, member_user = account(identity_app, f"proj_{kind}_{change}_m")
    outsider, _ = account(identity_app, f"proj_{kind}_{change}_x")
    created = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "projection-project"},
        json={"name": "嵌套子文档隔离", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert isinstance(project["id"], str) and int(project["id"]) > 2**53
    assert isinstance(project["primary_canvas_id"], str)
    join(identity_app, owner, member, project["id"], member_user["id"])
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    document = read_document(owner, path)["source_document"]
    items = [
        {"id": "entry-a", "name": "共享作品 A", **private_fields("owner", "a")},
        {"id": "entry-b", "name": "共享作品 B", **private_fields("owner", "b")},
    ]
    document["nodes"] = [
        {
            "id": "work",
            "type": "text",
            "title": "共享作品",
            "position": {"x": 20, "y": 20},
            "width": 320,
            "height": 220,
            "metadata": {"content": "保留的共享正文"},
        }
    ]
    if kind == "storyboard":
        document["nodes"][0]["metadata"]["storyboard"] = {"rows": items}
    elif kind == "timeline":
        document["timeline"] = {"clips": items}
    else:
        document["directorScenes"] = [{"id": "scene-main", "name": "共享场景", "objects": items}]
    owner_saved = commit_document(owner, path, document, "projection-owner-seed")
    own = read_document(owner, path)["source_document"]
    assert entries(own, kind) == items
    shared_before = read_document(member, path, private=False)
    assert_public(entries(shared_before["source_document"], kind))

    member_document = read_document(member, path)["source_document"]
    member_items = entries(member_document, kind)
    assert_public(member_items)
    for item in member_items:
        item.update(private_fields("member", item["id"].removeprefix("entry-")))
    member_private_saved = commit_document(member, path, member_document, "projection-member-own")
    assert member_private_saved["row_version"] == owner_saved["row_version"]
    assert read_document(member, path, private=False) == shared_before
    assert entries(read_document(owner, path)["source_document"], kind) == items

    member_document = read_document(member, path)["source_document"]
    changed_entries = entries(member_document, kind)
    if change == "reorder":
        changed_entries.reverse()
    else:
        changed_entries.pop(0)
    for item in changed_entries:
        item["name"] += "，成员修改"
    public_saved = commit_document(member, path, member_document, "projection-member-work")
    assert int(public_saved["row_version"]) == int(owner_saved["row_version"]) + 1
    expected_ids = [item["id"] for item in changed_entries]
    owner_current = read_document(owner, path)["source_document"]
    expected_owner = [
        {"id": item["id"], "name": item["name"], **private_fields("owner", item["id"][-1])}
        for item in changed_entries
    ]
    assert entries(owner_current, kind) == expected_owner
    assert [item["id"] for item in entries(owner_current, kind)] == expected_ids
    assert entries(read_document(member, path)["source_document"], kind) == changed_entries
    assert_public(entries(read_document(owner, path, private=False)["source_document"], kind))

    initial_history = owner.get(path + "/revisions")
    assert initial_history.status_code == 200, initial_history.text
    original = initial_history.json()["items"][-1]
    restore_key = "projection-owner-restore"
    restore_payload = {"expected_row_version": public_saved["row_version"]}
    restore_path = path + f"/revisions/{original['id']}/restore"
    restored = owner.post(
        restore_path, headers={"Idempotency-Key": restore_key}, json=restore_payload
    )
    assert restored.status_code == 200, restored.text
    restored_result = restored.json()
    assert read_document(owner, path, private=False)["source_document"]["nodes"] == []
    history = owner.get(path + "/revisions").json()["items"]
    frozen = next(item for item in history if item["row_version"] == public_saved["row_version"])
    assert isinstance(frozen["id"], str) and int(frozen["id"]) > 2**53
    assert isinstance(frozen["row_version"], str)
    frozen_path = path + f"/revisions/{frozen['id']}"
    frozen_owner = owner.get(frozen_path)
    assert frozen_owner.status_code == 200, frozen_owner.text
    assert entries(frozen_owner.json()["source_document"], kind) == expected_owner
    frozen_member = member.get(frozen_path)
    assert frozen_member.status_code == 200, frozen_member.text
    assert_public(entries(frozen_member.json()["source_document"], kind))

    with identity_app[1]() as session:
        frozen_row = session.get(CanvasRevision, int(frozen["id"]))
        original_snapshot = deepcopy(frozen_row.snapshot_json)
        private_row = session.scalar(
            select(CanvasRevisionUserState).where(
                CanvasRevisionUserState.revision_id == int(frozen["id"]),
                CanvasRevisionUserState.user_id == int(owner_user["id"]),
            )
        )
        assert private_row is not None
        original_private = deepcopy(private_row.state_json)
        receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.actor_user_id == int(owner_user["id"]),
                CanvasWriteReceipt.idempotency_key == restore_key,
            )
        )
        original_receipt = deepcopy(receipt.result_json)
    assert_public(entries(original_snapshot, kind))
    receipt_path = "/api/v1/canvas-write-receipts/" + restore_key
    assert owner.get(receipt_path).json()["result"] == restored_result
    assert member.get(receipt_path).status_code == 404
    assert outsider.get(receipt_path).status_code == 404

    member_restore_payload = {"expected_row_version": restored_result["row_version"]}
    member_restore = member.post(
        frozen_path + "/restore",
        headers={"Idempotency-Key": "projection-member-restore"},
        json=member_restore_payload,
    )
    assert member_restore.status_code == 200, member_restore.text
    member_receipt_path = "/api/v1/canvas-write-receipts/projection-member-restore"
    assert member.get(member_receipt_path).json()["result"] == member_restore.json()
    assert owner.get(member_receipt_path).status_code == 404
    assert outsider.get(member_receipt_path).status_code == 404
    member_after_restore = read_document(member, path)["source_document"]
    assert_public(entries(member_after_restore, kind))
    assert [item["id"] for item in entries(member_after_restore, kind)] == expected_ids
    assert owner.get(frozen_path).content == frozen_owner.content
    assert member.get(frozen_path).content == frozen_member.content
    owner_current = read_document(owner, path)["source_document"]
    for item in entries(owner_current, kind):
        item.update(private_fields("owner", "after-restore"))
    commit_document(owner, path, owner_current, "projection-owner-private-after-history")
    assert owner.get(frozen_path).content == frozen_owner.content

    before_replay = read_document(member, path, private=False)
    replay = owner.post(
        restore_path, headers={"Idempotency-Key": restore_key}, json=restore_payload
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == restored_result
    assert read_document(member, path, private=False) == before_replay
    assert owner.get(receipt_path).json()["result"] == restored_result
    for suffix in ("", "/my-document", "/revisions", f"/revisions/{frozen['id']}"):
        assert outsider.get(path + suffix).status_code == 404
    forbidden = outsider.post(
        frozen_path + "/restore",
        headers={"Idempotency-Key": "projection-outsider-restore"},
        json={"expected_row_version": before_replay["row_version"]},
    )
    assert forbidden.status_code == 404, forbidden.text
    assert read_document(member, path, private=False) == before_replay

    removed = owner.delete(f"/api/v1/projects/{project['id']}/members/{member_user['id']}")
    assert removed.status_code == 200, removed.text
    for suffix in ("", "/my-document", "/revisions", f"/revisions/{frozen['id']}"):
        inaccessible = member.get(path + suffix)
        assert inaccessible.status_code == 404, inaccessible.text
    assert member.get(member_receipt_path).status_code == 404
    for key, payload in (
        ("projection-member-restore", member_restore_payload),
        ("projection-revoked-restore", {"expected_row_version": before_replay["row_version"]}),
    ):
        rejected = member.post(
            frozen_path + "/restore", headers={"Idempotency-Key": key}, json=payload
        )
        assert rejected.status_code == 404, rejected.text
    assert read_document(owner, path, private=False) == before_replay
    assert owner.get(frozen_path).content == frozen_owner.content
    assert owner.get(receipt_path).json()["result"] == restored_result
    with identity_app[1]() as session:
        assert session.get(CanvasRevision, int(frozen["id"])).snapshot_json == original_snapshot
        assert session.get(CanvasRevisionUserState, private_row.id).state_json == original_private
        assert session.get(CanvasWriteReceipt, receipt.id).result_json == original_receipt
        member_receipt = session.scalar(
            select(CanvasWriteReceipt).where(
                CanvasWriteReceipt.actor_user_id == int(member_user["id"]),
                CanvasWriteReceipt.idempotency_key == "projection-member-restore",
            )
        )
        assert member_receipt is not None
        assert member_receipt.result_json == member_restore.json()
    assert database_errors == []
