"""嵌套私人子文档的真实 HTTP/MySQL 隔离、稳定身份与历史不可变性。"""

from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from short_drama.domain.canvas.document.canvas import (
    CanvasRevision,
    CanvasRevisionUserState,
    CanvasWriteReceipt,
)
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.standard.identity.test_identity_collaboration import account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

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
def test_nested_private_state_survives_owner_edits_without_entering_history_or_receipts(
    identity_app: Any, database_errors: list, kind: str, change: str
) -> None:
    owner, owner_user = account(identity_app, f"proj_{kind}_{change}_o")
    outsider, _ = account(identity_app, f"proj_{kind}_{change}_x")
    created = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "projection-project"},
        json={"name": "嵌套子文档隔离", "workspace_mode": "infinite_canvas"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert isinstance(project["id"], str) and int(project["id"]) > 2**53
    assert isinstance(project["primary_canvas_id"], str)
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    document = read_document(owner, path)["source_document"]
    items = [
        {"id": "entry-a", "name": "作品 A", **private_fields("owner", "a")},
        {"id": "entry-b", "name": "作品 B", **private_fields("owner", "b")},
    ]
    document["nodes"] = [
        {
            "id": "work",
            "type": "text",
            "title": "作品",
            "position": {"x": 20, "y": 20},
            "width": 320,
            "height": 220,
            "metadata": {"content": "保留的作品正文"},
        }
    ]
    if kind == "storyboard":
        document["nodes"][0]["metadata"]["storyboard"] = {"rows": items}
    elif kind == "timeline":
        document["timeline"] = {"clips": items}
    else:
        document["directorScenes"] = [{"id": "scene-main", "name": "场景", "objects": items}]
    owner_saved = commit_document(owner, path, document, "projection-owner-seed")
    own = read_document(owner, path)["source_document"]
    assert entries(own, kind) == items
    public_before = read_document(owner, path, private=False)
    assert_public(entries(public_before["source_document"], kind))

    for item in entries(own, kind):
        item["prompt"] += " 更新"
    private_saved = commit_document(owner, path, own, "projection-owner-private")
    assert private_saved["row_version"] == owner_saved["row_version"]
    assert read_document(owner, path, private=False) == public_before
    changed_document = read_document(owner, path)["source_document"]
    changed_entries = entries(changed_document, kind)
    if change == "reorder":
        changed_entries.reverse()
    else:
        changed_entries.pop(0)
    for item in changed_entries:
        item["name"] += "，本人修改"
    public_saved = commit_document(owner, path, changed_document, "projection-owner-work")
    assert int(public_saved["row_version"]) == int(owner_saved["row_version"]) + 1
    assert entries(read_document(owner, path)["source_document"], kind) == changed_entries
    assert_public(entries(read_document(owner, path, private=False)["source_document"], kind))

    history = owner.get(path + "/revisions").json()["items"]
    original = history[-1]
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
    assert entries(frozen_owner.json()["source_document"], kind) == changed_entries
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
    assert outsider.get(receipt_path).status_code == 404

    recovered = owner.post(
        frozen_path + "/restore",
        headers={"Idempotency-Key": "projection-owner-recover"},
        json={"expected_row_version": restored_result["row_version"]},
    )
    assert recovered.status_code == 200, recovered.text
    owner_current = read_document(owner, path)["source_document"]
    assert entries(owner_current, kind) == changed_entries
    for item in entries(owner_current, kind):
        item.update(private_fields("owner", "after-restore"))
    commit_document(owner, path, owner_current, "projection-owner-private-after-history")
    assert owner.get(frozen_path).content == frozen_owner.content
    before_replay = read_document(owner, path, private=False)
    replay = owner.post(
        restore_path, headers={"Idempotency-Key": restore_key}, json=restore_payload
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == restored_result
    assert read_document(owner, path, private=False) == before_replay
    for suffix in ("", "/my-document", "/revisions", f"/revisions/{frozen['id']}"):
        assert outsider.get(path + suffix).status_code == 404
    forbidden = outsider.post(
        frozen_path + "/restore",
        headers={"Idempotency-Key": "projection-outsider-restore"},
        json={"expected_row_version": before_replay["row_version"]},
    )
    assert forbidden.status_code == 404, forbidden.text
    assert read_document(owner, path, private=False) == before_replay
    assert owner.get(frozen_path).content == frozen_owner.content
    assert owner.get(receipt_path).json()["result"] == restored_result
    with identity_app[1]() as session:
        assert session.get(CanvasRevision, int(frozen["id"])).snapshot_json == original_snapshot
        assert session.get(CanvasRevisionUserState, private_row.id).state_json == original_private
        assert session.get(CanvasWriteReceipt, receipt.id).result_json == original_receipt
    assert database_errors == []
